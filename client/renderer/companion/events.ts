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
import {
  beginAutoVoiceTurn,
  cancelAutoVoice,
  endAutoVoiceTurn,
  feedAutoVoiceDelta,
  flushAutoVoiceSegments,
  isAutoVoiceActive
} from '@/companion/auto-voice-stream'
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
  hydrateChatMessages,
  pushAffectTraceMessage,
  pushMediaMessage,
  pushProactiveMessage,
  setAssistantError,
  setAssistantTool,
  setChatOpen,
  setSessionContextUsage,
  setTurnHadBubbleBreak,
  showMediaHint,
  submitPendingBatch
} from '@/companion/chat-store'
import {
  $effectiveTier,
  $spriteState,
  clearGazeTarget,
  playSpriteActionSequence,
  setGazeTarget,
  setSpriteState,
  type SpriteEmotion
} from '@/companion/companion-store'
import { resetExpressionAvatars } from '@/companion/expression-avatar/expression-avatar-store'
import { hydrateMesh2D, resetMesh2D, setMesh2DStatus, switchRenderMode } from '@/companion/mesh2d/mesh2d-store'
import { $responseMode } from '@/companion/prefs'
import { hydratePuppet, resetPuppet } from '@/companion/puppet/puppet-store'
import { switchSession } from '@/companion/session-list-store'
import { $defaultScale, computePerchPlacement, setLocale, startRoam } from '@/companion/spatial'
import { speak } from '@/companion/tts'
import { emitVfx } from '@/companion/vfx'
import { hydrateWardrobe } from '@/companion/wardrobe/wardrobe-store'
import { log } from '@/shared/lib/log'
import { sleep } from '@/shared/lib/utils'
import { $gateway } from '@/shared/store/gateway'
import { notify } from '@/shared/store/notifications'
import type { ChatMediaItem, RpcEvent, SessionMessage } from '@/shared/types/spiritagent'

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
  // 空间 cue 是云端语义的移动指令，只有自主档兑现（DESIGN §3.5）——常规不移动，静止不做任何主动表达。
  if (!locale || $screenLocked.get() || $effectiveTier.get() !== 'autonomous') {
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

      if ($responseMode.get() === 'voice' && !$screenLocked.get()) {
        beginAutoVoiceTurn()
      }

      break
    case 'message.delta': {
      const text = (event.payload as { text?: string } | undefined)?.text ?? ''

      if (text) {
        appendAssistantDelta(text)

        if ($responseMode.get() === 'voice' && !$screenLocked.get()) {
          feedAutoVoiceDelta(text)
        }
      }

      break
    }

    case 'message.break': {
      // 后端把回合切成了连续的气泡——收尾当前气泡；
      // 下一条 message.delta 会开一个新气泡（后端已在它们之间插入 0.5–1.5 秒停顿）。
      setTurnHadBubbleBreak(true)
      finalizeAssistantMessage()
      flushAutoVoiceSegments()

      break
    }

    case 'message.complete': {
      const payload = event.payload as
        | {
            text?: string
            media?: ChatMediaItem[]
            affect?: { emotion?: string; actions?: string[]; locale?: string; target?: string }
            usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number }
          }
        | undefined

      const text = payload?.text ?? ''
      const emotion = payload?.affect?.emotion
      const actions = payload?.affect?.actions ?? []
      const locale = payload?.affect?.locale
      const target = payload?.affect?.target

      if (payload?.usage) {
        setSessionContextUsage({
          promptTokens: payload.usage.prompt_tokens,
          completionTokens: payload.usage.completion_tokens,
          totalTokens:
            payload.usage.total_tokens ??
            (payload.usage.prompt_tokens && payload.usage.completion_tokens
              ? payload.usage.prompt_tokens + payload.usage.completion_tokens
              : undefined)
        })
      }

      // 锁屏状态下，抑制渲染层的提示。
      const screenLocked = $screenLocked.get()

      // "neutral" 是 LLM 的无操作情绪；当作无 affect 处理，避免触发徽标闪烁。
      // 情绪通道不受锁屏拦截（DESIGN §6.2）；锁屏只静默语音与消息。
      const hasEmotion = Boolean(emotion && emotion !== 'neutral')

      // 多气泡回合：每个气泡各自携带流式文本；
      // payload.text 是整轮（包含两个气泡）的全文，会覆盖最后一个气泡。
      // 这种情况下保留 last.text。媒体与正文正交，始终挂到最后一格。
      finalizeAssistantMessage($turnHadBubbleBreak.get() ? undefined : payload?.text, payload?.media)

      // 媒体已送达但聊天窗收起：气泡只做轻量提示，点击打开聊天窗查看（富媒体统一在对话窗展示）。
      if (payload?.media?.length && !$chatOpen.get() && !screenLocked) {
        showMediaHint(
          payload.media.some(m => m.type === 'video')
            ? '🎬 我生成了一段视频，点这里查看'
            : '🖼️ 我生成了一张图片，点这里查看'
        )
      }

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

      // 在「始终语音」模式下，流式语音队列在 message.complete 时收尾并排干残句；
      // 若中途切为语音模式或队列未启动且有文本，走 speak() 兜底。非语音模式或锁屏时中止。
      if ($responseMode.get() === 'voice' && !screenLocked) {
        if (isAutoVoiceActive()) {
          endAutoVoiceTurn()
        } else if (text.trim()) {
          setSpriteState('speaking')
          void speak(text).then(() => {
            if ($spriteState.get() === 'speaking') {
              setSpriteState('idle', { force: true })
            }
          })
        }
      } else {
        cancelAutoVoice()
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

      // 情绪通道不受锁屏拦截（DESIGN §6.2）；静止档经防御性跳过——后端源头已断流，此处兜底。
      if (emotion && emotion !== 'neutral' && $effectiveTier.get() !== 'still') {
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
      // 2d 拆分完成——重新水合 2d 行并串一次 puppet 分流判定（manifest 恒为 kind=psd 描述符）。
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

      void hydrateMesh2D().then(() => hydratePuppet())

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
        void hydrateMesh2D().then(() => hydratePuppet())
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
      resetPuppet()
      void hydrateMesh2D().then(() => hydratePuppet())

      break
    }

    case 'error': {
      cancelAutoVoice()
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

      // Affect 在文本之前流动，这样即便文本被抑制，反应仍能显示；静止档连主动情绪一并停
      // （后端源头已拦，此处兜底非官方链路）。
      if (affectEmotion && affectEmotion !== 'neutral' && currentTier !== 'still') {
        setSpriteState('emotional', { emotion: affectEmotion as SpriteEmotion })
      }

      // 静止档与锁屏会抑制气泡。
      const textSuppressed = currentTier === 'still' || $screenLocked.get()

      if (text && !textSuppressed) {
        void speakProactive(text, { affect: affectEmotion })

        if ($chatOpen.get()) {
          pushProactiveMessage(text)
        }
      }

      break
    }

    case 'video_gen.completed': {
      // 后台视频任务完成（WSEvent outbox 路径，信封不带 session_id，载荷自带）。
      const p = event.payload as
        | { task_id?: string; url?: string; session_id?: string; media?: ChatMediaItem[] }
        | undefined

      const sessionId = p?.session_id
      const media: ChatMediaItem[] = p?.media?.length ? p.media : p?.url ? [{ type: 'video', url: p.url }] : []

      if (!media.length) {
        break
      }

      if (sessionId && sessionId === $chatSessionId.get()) {
        pushMediaMessage(media)
      } else if (!$screenLocked.get()) {
        // 正在看别的会话时用通知承载跳转；聊天窗收起时用精灵气泡提示。
        if ($chatOpen.get() && sessionId) {
          notify({
            kind: 'success',
            message: '视频生成好了',
            action: {
              label: '查看',
              onClick: () => {
                setChatOpen(true)
                void switchSession(sessionId)
              }
            }
          })
        } else {
          showMediaHint('🎬 视频生成好了，点这里查看', sessionId)
        }
      }

      break
    }

    case 'video_gen.failed': {
      const p = event.payload as { error?: string } | undefined

      if (p?.error && !$screenLocked.get()) {
        notify({ kind: 'warning', message: p.error })
      }

      break
    }

    case 'channel.status': {
      // IM 通道绑定状态变化（outbox；Hub 设置页以 REST 为真相源，这里只做桌面提醒）。
      const p = event.payload as { channel?: string; status?: string; error?: string } | undefined
      const label = p?.channel === 'weixin_ilink' ? '微信' : 'IM 通道'

      const text =
        p?.status === 'connected'
          ? `${label}已连接`
          : p?.status === 'login_required'
            ? `${label}登录已过期，请到设置重新扫码`
            : p?.status === 'error'
              ? `${label}通道异常${p?.error ? `：${p.error}` : ''}`
              : null

      if (text && !$screenLocked.get()) {
        notify({ kind: p?.status === 'error' ? 'error' : 'info', message: text })
      }

      break
    }

    case 'channel.peer_request': {
      // 陌生对端首次来信（outbox）：提示主人到设置「聊天通道」审批。
      const p = event.payload as
        | { channel?: string; peer_id?: string; peer_name?: string; preview?: string }
        | undefined

      const label = p?.channel === 'weixin_ilink' ? '微信' : 'IM'
      const name = p?.peer_name || p?.peer_id || ''

      if (!$screenLocked.get()) {
        notify({
          kind: 'info',
          message: `${label}上有人想和伙伴聊天${name ? `：${name}` : ''}`,
          detail: p?.preview
        })
      }

      break
    }

    case 'command.result': {
      // 服务端在 command.dispatch RPC response 之外另行广播此事件（PROTOCOL §1.3）；
      // 触发它的窗口已通过 RPC 路径自己渲染过 pill，本路径只服务其他窗口的同步渲染。
      // RPC 路径的 pushStatusPill 已在 chat-dock 的 executeSlashCommand 中幂等执行。
      const payload = event.payload as
        | {
            command?: string
            result?: { status: 'ok' | 'error'; message: string; payload?: unknown; hydrate?: boolean }
          }
        | undefined

      const r = payload?.result

      if (!r) {
        break
      }

      // 仅同步 status_cleared / compress_summary 等历史变化（hydrate=true）：
      // 其他窗口需要本地 hydrateChatMessages，否则会显示陈旧消息列表。
      if (r.status === 'ok' && r.hydrate) {
        const messages = (r.payload as { messages?: unknown } | undefined)?.messages

        if (Array.isArray(messages)) {
          hydrateChatMessages(messages as SessionMessage[])
        }
      }

      break
    }

    default:
      break
  }
}
