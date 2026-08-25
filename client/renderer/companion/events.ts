import {
  $clipMap,
  $modelGenError,
  $modelGenProgress,
  $modelGenState,
  clearModelRetry,
  hydrateExpressions,
  setModelFailed,
  setModelInfo
} from '@/companion/3d/model-store'
import { $screenLocked } from '@/companion/activity'
import { reportInteractionStat } from '@/companion/activity'
import { resolveAvatarRegeneration } from '@/companion/avatar-regen-store'
import {
  $chatOpen,
  $chatSessionId,
  $chatTurnInFlight,
  $turnHadBubbleBreak,
  appendAssistantDelta,
  beginAssistantMessage,
  clearPendingPrompts,
  finalizeAssistantMessage,
  pushAffectTraceMessage,
  pushProactiveMessage,
  setAssistantError,
  setAssistantTool,
  setTurnHadBubbleBreak,
  submitPendingBatch
} from '@/companion/chat-store'
import {
  $effectiveTier,
  $voiceCallOpen,
  clearGazeTarget,
  playSpriteActionSequence,
  setGazeTarget,
  setSpriteState,
  type SpriteEmotion
} from '@/companion/companion-store'
import { resetExpressionAvatars } from '@/companion/expression-avatar/expression-avatar-store'
import { hydrateMesh2D, resetMesh2D, setMesh2DStatus, switchRenderMode } from '@/companion/mesh2d/mesh2d-store'
import { emitVfx } from '@/companion/mesh2d/mesh2d-vfx'
import { $responseMode } from '@/companion/prefs'
import { $defaultScale, computePerchPlacement, setLocale, startRoam } from '@/companion/spatial'
import { speak } from '@/companion/tts'
import { hydrateWardrobe } from '@/companion/wardrobe/wardrobe-store'
import { log } from '@/shared/lib/log'
import { sleep } from '@/shared/lib/utils'
import { $gateway } from '@/shared/store/gateway'
import type { RpcEvent } from '@/shared/types/spiritagent'

import { $devMode, pushDevLog } from './developer-overlay'
import { speakProactive } from './proactive/proactive'
import { findWindowByKeyword, gazeTowardsPoint, performRitualWalk, type WindowGeom } from './ritual-walk'

const PERCH_RETRY_MS = 300
const PERCH_RETRY_COUNT = 5
// click_at 虚拟目标几何的边长（px）：只为 perch 落位与指向方位提供参照，
// 精灵会站到点击点旁而非覆盖它。
const CLICK_GEOM_SIZE = 160
const CLICK_GEOM_HALF = CLICK_GEOM_SIZE / 2

// 高唤醒负面情绪冒冷汗（DESIGN §6.3 粒子清单 💦 的情绪侧触发点）
const SWEAT_EMOTIONS: ReadonlySet<string> = new Set(['scared', 'embarrassed', 'concerned', 'apologetic'])

function maybeEmotionVfx(emotion?: string): void {
  if (emotion && SWEAT_EMOTIONS.has(emotion)) {
    emitVfx('sweat', { nx: 0.5, ny: 0.2, count: 2 })
  }
}

async function findWindowWithRetry(keyword: string): Promise<WindowGeom | null> {
  for (let attempt = 0; attempt <= PERCH_RETRY_COUNT; attempt++) {
    const geom = await findWindowByKeyword(keyword)

    if (geom) {
      return geom
    }

    if (attempt < PERCH_RETRY_COUNT) {
      await sleep(PERCH_RETRY_MS)
    }
  }

  return null
}

function applySpatialCue(locale?: string, target?: string): void {
  if (!locale || $screenLocked.get() || $effectiveTier.get() === 'quiet') {
    return
  }

  // 不要在聊天面板打开时把精灵拽走。
  if ($chatOpen.get() && (locale === 'home' || locale === 'roam')) {
    return
  }

  void (async () => {
    if (locale === 'perch' && target) {
      const geom = await findWindowWithRetry(target)

      if (!geom) {
        return
      }

      const perch = computePerchPlacement(geom, $defaultScale.get())

      if (!perch) {
        return
      }

      // 与仪式行走同规则：飞行与栖息途中视线锁定目标窗口，数秒后交还指针跟随
      setGazeTarget(gazeTowardsPoint({ x: geom.x + geom.w / 2, y: geom.y + geom.h / 2 }))
      setTimeout(() => clearGazeTarget(), 6000)
      setLocale('perch', { position: perch.pos, scaleLimit: perch.scale, locomotion: 'fly' })
    } else if (locale === 'home' && !$chatOpen.get()) {
      setLocale('home', { locomotion: 'fly' })
    } else if (locale === 'roam') {
      startRoam()
    }
  })().catch(err => {
    log.error('events', 'applySpatialCue error:', err)
  })
}

export function handleCompanionEvent(event: RpcEvent): void {
  if ($devMode.get()) {
    pushDevLog(event.type, JSON.stringify(event.payload ?? {}))
  }

  // 聊天回合事件（message.start/delta/complete、tool.*、error）携带发出该事件的会话 session_id。
  // 来自渲染层当前未查看会话的事件不应作用于可见聊天——
  // 例如 cron 的自动回合通过 cron 会话流式输出文本；没有这道门的话，
  // 用户会看到 cron 的回复，好像它回答了主会话上一条消息。
  // WSEvent 驱动的事件（companion.message/affect、model.*、
  // avatar.regenerated）没有 session_id，直接放行。
  if (event.session_id !== undefined) {
    const current = $chatSessionId.get()

    if (current === null || event.session_id !== current) {
      return
    }
  }

  switch (event.type) {
    case 'message.start':
      beginAssistantMessage()
      setTurnHadBubbleBreak(false)
      setSpriteState('thinking')

      break
    case 'message.delta': {
      const text = (event.payload as { text?: string } | undefined)?.text ?? ''

      if (text) {
        appendAssistantDelta(text)
      }

      break
    }

    case 'message.break': {
      // 后端把回合切成了连续的气泡——收尾当前气泡；
      // 下一条 message.delta 会开一个新气泡（后端已在它们之间插入 0.5–1.5 秒停顿）。
      setTurnHadBubbleBreak(true)
      finalizeAssistantMessage()

      break
    }

    case 'message.complete': {
      const payload = event.payload as
        | { text?: string; affect?: { emotion?: string; actions?: string[]; locale?: string; target?: string } }
        | undefined

      const text = payload?.text ?? ''
      const emotion = payload?.affect?.emotion
      const actions = payload?.affect?.actions ?? []
      const locale = payload?.affect?.locale
      const target = payload?.affect?.target

      // 锁屏状态下，抑制渲染层的提示。
      const screenLocked = $screenLocked.get()

      // "neutral" 是 LLM 的无操作情绪；当作无 affect 处理，避免触发徽标闪烁。
      // 情绪通道不受锁屏拦截（DESIGN §6.2「断消息不断情绪」）；锁屏只静默语音与消息。
      const hasEmotion = Boolean(emotion && emotion !== 'neutral')

      // 多气泡回合：每个气泡各自携带流式文本；
      // payload.text 是整轮（包含两个气泡）的全文，会覆盖最后一个气泡。
      // 这种情况下保留 last.text。
      finalizeAssistantMessage($turnHadBubbleBreak.get() ? undefined : payload?.text)

      // DESIGN §6.6 场景 1：纯情绪/动作回合无正文，空气泡已被上面剪掉——
      // 补一条情绪痕迹行，与后端持久化的 status_affect 行保持一致。
      if (!text.trim() && (hasEmotion || actions.length > 0)) {
        pushAffectTraceMessage()
      }

      if (hasEmotion) {
        maybeEmotionVfx(emotion)
        setSpriteState('emotional', { emotion: emotion as SpriteEmotion, action: actions[0] })
        playSpriteActionSequence(actions)
      } else {
        setSpriteState('idle', { force: true })
      }

      applySpatialCue(locale, target)

      // 在「始终语音」模式下朗读聊天回复（plan §4.1）；
      // 通话进行中或锁屏时跳过。延后一帧让 EMOTIONAL 先可观察，
      // 再被 SPEAKING 覆盖（ARCH §7.5）。
      if ($responseMode.get() === 'voice' && text.trim() && !$voiceCallOpen.get() && !screenLocked) {
        const say = () => void speak(text).then(() => setSpriteState('idle', { force: true }))

        if (hasEmotion) {
          setTimeout(() => {
            setSpriteState('speaking')
            say()
          }, 1200)
        } else {
          setSpriteState('speaking')
          say()
        }
      }

      // 每日互动统计——chat_turn 仅在确有文本可统计时计数
      // （与上面的 TTS 门控一致）。fire-and-forget RPC 由 activity.ts 中的公共助手负责。
      if (text.trim()) {
        reportInteractionStat('chat_turn')
      }

      // in-flight 回合结束——清掉标记并冲刷用户在回合运行期间排队的消息
      // （合并为单次批量提交）。
      $chatTurnInFlight.set(false)
      submitPendingBatch()

      break
    }

    case 'companion.affect': {
      // 云端独立 affect 推送按 DESIGN §6.6 只承载情绪与空间；动作序列走 message.complete 的 affect.actions。
      const payload = event.payload as { emotion?: string; locale?: string; target?: string } | undefined

      const emotion = payload?.emotion
      const locale = payload?.locale
      const target = payload?.target

      // 情绪通道不受锁屏拦截（DESIGN §6.2「断消息不断情绪」）
      if (emotion && emotion !== 'neutral') {
        maybeEmotionVfx(emotion)
        setSpriteState('emotional', { emotion: emotion as SpriteEmotion })
      }

      applySpatialCue(locale, target)

      break
    }

    case 'tool.start': {
      // 全局 WORKING 入口——所有工具（后端 / memory / runner）
      // 在执行开始前都会发 tool_start，因此无论工具位置如何精灵都会进入 WORKING。
      // tool.call（下方）只针对 Runner 工具触发，并携带 IPC 分发所需的参数。
      const p = event.payload as { name?: string } | undefined

      setAssistantTool(p?.name ?? '工具')
      setSpriteState('working')

      break
    }

    case 'tool.call': {
      // 仅 Runner 分发——WORKING 已由 tool.start 设置。
      // tool.call 携带 Runner IPC 所需的参数；缺少 bridge 或 call_id
      // 时后端的 await_future 会在 300 秒后超时并上报错误。
      const p = (event.payload as { name?: string; args?: Record<string, unknown>; call_id?: string } | undefined) ?? {}

      const runnerInvoke = window.spiritagent?.runnerInvoke

      if (!p.call_id || !runnerInvoke) {
        break
      }

      const name = p.name ?? ''

      // fire-and-forget 调用 Runner 并把结果回传，让后端的
      // await_future 解析完成；工具错误不得冒泡到本处理器。
      const gateway = $gateway.get()

      void (async () => {
        try {
          const args = p.args ?? {}

          // 仪式行走的解析目标：
          // - system.click_at 的目标就是点击坐标本身（包成虚拟窗口几何，走到旁边
          //   后 execute 即那次点击，不再额外补一次 click → 双击）；
          // - open_application / browser_* 按名称或 URL 匹配既有窗口；
          //   关键词缺失时重试也不会有结果，直接走常规调用。
          let findTarget: (() => Promise<WindowGeom | null>) | null = null
          let previewClick = true

          if (name === 'system.click_at') {
            const cx = Number(args.x)
            const cy = Number(args.y)

            if (Number.isFinite(cx) && Number.isFinite(cy)) {
              const geom: WindowGeom = {
                x: cx - CLICK_GEOM_HALF,
                y: cy - CLICK_GEOM_HALF,
                w: CLICK_GEOM_SIZE,
                h: CLICK_GEOM_SIZE
              }

              findTarget = () => Promise.resolve(geom)
              previewClick = false
            }
          } else {
            const keyword = String(args.name ?? args.url ?? args.keyword ?? '')

            if (keyword.trim()) {
              findTarget = () => findWindowByKeyword(keyword)
            }
          }

          const result = findTarget
            ? await performRitualWalk(findTarget, () => runnerInvoke(name, args), { previewClick })
            : await runnerInvoke(name, args)

          await gateway?.request('tool.result', { call_id: p.call_id, result })
        } catch (err) {
          try {
            // DESIGN §6.5「Runner 宕机人格化拒绝层」：原始错误不回传 LLM——
            // message 可能含路径/系统调用细节，LLM 可能照念给用户。诚实（承认没做到）
            // 但不暴露技术细节；原始错误只进本地日志留痕。
            log.warn('events', `runner tool ${name} failed:`, err)
            await gateway?.request('tool.result', {
              call_id: p.call_id,
              result: { ok: false, error: '（手没回应：本机执行器没有完成这次操作）' }
            })
          } catch {
            /* 尽力而为——后端的 300 秒兜底会处理 */
          }
        }
      })()

      break
    }

    case 'tool.complete': {
      // 全局 WORKING 出口——所有工具的 finally 块都会发 tool_end。
      // force：THINKING（50）< WORKING（70），没有 force 的话优先级门控会静默拒绝该转换。
      setAssistantTool(null)
      setSpriteState('thinking', { force: true })

      break
    }

    case 'model.ready': {
      // 后端在 /api/companion/model 生成结束后推送此事件。
      // 只要 $modelInfo.asset_url 变化，3D 引擎就会重新加载（见 companion-3d.tsx）。
      // error 字段用于展示生成失败；目前 UI 只是记录日志，恢复流程在后续切片。
      const p = event.payload as
        | {
            model_id?: number
            asset_url?: string
            species?: string
            rig_type?: string
            style?: string
            content_hash?: string
            error?: string
            clip_map?: Readonly<Record<string, string>>
          }
        | undefined

      if (p?.error) {
        log.warn('events', 'model.ready error:', p.error)
        setModelFailed(p.error)

        break
      }

      $modelGenState.set('succeeded')
      $modelGenProgress.set(null)
      $modelGenError.set(null)
      clearModelRetry()
      setModelInfo({
        id: p?.model_id ?? null,
        asset_url: p?.asset_url ?? null,
        species: p?.species ?? null,
        rig_type: p?.rig_type ?? 'biped',
        style: p?.style ?? 'realistic',
        content_hash: p?.content_hash ?? null,
        status: 'succeeded',
        has_rig: true
      })
      // 运行时新生成的模型必须在此接住映射，否则角色会一直静止到下次水合。
      $clipMap.set(p?.clip_map ?? {})

      break
    }

    case 'model.gen.progress': {
      const p = event.payload as { stage?: string; progress?: number } | undefined

      // 终态已定后,迟到的 progress 不能再把它打回 'generating' —— 否则覆盖层会重现。
      if ($modelGenState.get() === 'succeeded') {
        break
      }

      $modelGenState.set(p?.stage === 'done' ? 'succeeded' : 'generating')
      $modelGenProgress.set({ stage: p?.stage ?? '', progress: p?.progress ?? 0 })

      break
    }

    case 'model.failed': {
      const p = event.payload as { reason?: string; retry_download?: boolean; model_id?: number } | undefined
      setModelFailed(p?.reason ?? '3D 模型生成失败', {
        retryDownload: p?.retry_download === true,
        modelId: p?.model_id ?? null
      })

      break
    }

    case 'companion.assets.updated': {
      // 伙伴即时创建了新表情（create_expression 工具）；重新拉取让聊天窗无需重启即可用上。
      void hydrateExpressions()

      break
    }

    case 'companion.2d.ready': {
      // 2d 切分完成——重新水合让 Mesh2DCanvas 立即接管显示。
      const p = event.payload as
        | {
            model_id?: number
            manifest_url?: string | null
            layers?: { name: string; url: string }[]
          }
        | undefined

      if (p?.manifest_url) {
        log.info('events', '2d ready:', p.model_id)
      }

      void hydrateMesh2D()

      break
    }

    case 'companion.2d.failed': {
      // 切分失败：渲染层由 SpriteStage 兜底（程序化蛋 / 已就绪的 3D 模型）。
      const p = event.payload as { reason?: string } | undefined
      setMesh2DStatus('failed', p?.reason ?? '2D 切分失败')
      log.warn('events', '2d failed:', p?.reason)

      break
    }

    case 'companion.outfit.updated': {
      // 衣柜状态变化（切分就绪/穿着翻转/删除）——重拉列表；列表端点是真相源，事件只当刷新触发。
      // 仅穿着翻转时重水合 2d（幂等，与 2d.ready 双触发无妨）；入柜不换装与删除不动当前穿着。
      const p = event.payload as { worn?: boolean } | undefined

      void hydrateWardrobe()

      if (p?.worn) {
        void hydrateMesh2D()
      }

      break
    }

    case 'companion.outfit.failed': {
      const p = event.payload as { reason?: string } | undefined
      void hydrateWardrobe()
      log.warn('events', 'outfit failed:', p?.reason)

      break
    }

    case 'companion.render_mode.changed': {
      const p = event.payload as { new_mode?: '2d' | '3d' } | undefined

      if (p?.new_mode === '2d' || p?.new_mode === '3d') {
        void switchRenderMode(p.new_mode)
      }

      break
    }

    case 'avatar.regenerated': {
      // 后台重新生成的结果——通过 job_id 解析等待者，
      // 让肖像能直接替换而不阻塞处理器。
      const p = event.payload as
        | {
            job_id?: string
            asset_url?: string | null
            id?: number
            error?: string
          }
        | undefined

      if (p?.job_id) {
        resolveAvatarRegeneration(p)
      }

      // 头像身份已变化——表情头像的锚点已过期（按 avatar_id 过滤行），清空本地缓存。
      resetExpressionAvatars()

      // DESIGN §1.2 不变量：头像重生不使 2D/3D 模型失效——模型只随物种变更或用户
      // 显式请求重生。这里只做幂等的本地状态刷新（hydrate 重新拉取既有资产行）。
      resetMesh2D()
      void hydrateMesh2D()

      break
    }

    case 'error': {
      $chatTurnInFlight.set(false)
      clearPendingPrompts()
      // 强制重置为 idle——精灵在 'thinking' / 'working' 时，
      // 优先级门控会静默拒绝普通的状态转换。
      const message = (event.payload as { message?: string } | undefined)?.message ?? '出了点小问题'
      setAssistantError(message)
      setSpriteState('idle', { force: true })

      break
    }

    case 'companion.message': {
      const payload = event.payload as { text?: string; affect?: { emotion?: string } } | undefined
      const text = payload?.text ?? ''
      const currentTier = $effectiveTier.get()
      const affectEmotion = payload?.affect?.emotion

      // Affect 在文本之前流动，这样即便文本被抑制，反应仍能显示。
      if (affectEmotion && affectEmotion !== 'neutral') {
        setSpriteState('emotional', { emotion: affectEmotion as SpriteEmotion })
      }

      // 安静档位与锁屏会抑制气泡；上方的 affect 仍正常流动。
      const textSuppressed = currentTier === 'quiet' || $screenLocked.get()

      if (text && !textSuppressed) {
        void speakProactive(text, { affect: affectEmotion })

        if ($chatOpen.get()) {
          pushProactiveMessage(text)
        }
      }

      break
    }

    default:
      break
  }
}
