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
  pushProactiveMessage,
  setAssistantError,
  setAssistantTool,
  setTurnHadBubbleBreak,
  submitPendingBatch
} from '@/companion/chat-store'
import { $effectiveTier, $voiceCallOpen, setSpriteState, type SpriteEmotion } from '@/companion/companion-store'
import { resetExpressionAvatars } from '@/companion/expression-avatar/expression-avatar-store'
import { $responseMode } from '@/companion/prefs'
import { computePerchPosition, setLocale, startRoam } from '@/companion/spatial'
import { resetSpriteAlbum } from '@/companion/static-sprite/sprite-store'
import { speak } from '@/companion/tts'
import { log } from '@/shared/lib/log'
import { sleep } from '@/shared/lib/utils'
import { $gateway } from '@/shared/store/gateway'
import type { RpcEvent } from '@/shared/types/spiritagent'

import { $devMode, pushDevLog } from './developer-overlay'
import { speakProactive } from './proactive/proactive'
import { findWindowByKeyword, performRitualWalk, type WindowGeom } from './ritual-walk'

const PERCH_RETRY_MS = 300
const PERCH_RETRY_COUNT = 5

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

      const perch = computePerchPosition(geom)

      if (!perch) {
        return
      }

      setLocale('perch', { position: perch, locomotion: 'fly' })
    } else if (locale === 'sleep') {
      setLocale('sleep')
    } else if (locale === 'home' && !$chatOpen.get()) {
      setLocale('home', { locomotion: 'fly' })
    } else if (locale === 'chat') {
      setLocale('chat')
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
        | { text?: string; affect?: { emotion?: string; action?: string; locale?: string; target?: string } }
        | undefined

      const text = payload?.text ?? ''
      const emotion = payload?.affect?.emotion
      const action = payload?.affect?.action
      const locale = payload?.affect?.locale
      const target = payload?.affect?.target

      // 锁屏状态下，抑制渲染层的提示。
      const screenLocked = $screenLocked.get()

      // 多气泡回合：每个气泡各自携带流式文本；
      // payload.text 是整轮（包含两个气泡）的全文，会覆盖最后一个气泡。
      // 这种情况下保留 last.text。
      finalizeAssistantMessage($turnHadBubbleBreak.get() ? undefined : payload?.text)

      // "neutral" 是 LLM 的无操作情绪；当作无 affect 处理，避免触发徽标闪烁。
      const hasEmotion = Boolean(emotion && emotion !== 'neutral')

      if (hasEmotion && !screenLocked) {
        setSpriteState('emotional', { emotion: emotion as SpriteEmotion, action })
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
      // 来自 LLM 或后端的 affect 与空间具身提示：
      const payload = event.payload as { emotion?: string; locale?: string; target?: string } | undefined
      const emotion = payload?.emotion
      const locale = payload?.locale
      const target = payload?.target

      if (emotion && emotion !== 'neutral' && !$screenLocked.get()) {
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
          const isInteractiveTool =
            name === 'system.open_application' || name.startsWith('browser_') || name === 'system.click_at'

          const result = isInteractiveTool
            ? await performRitualWalk(
                () => findWindowByKeyword(String(p.args?.name ?? p.args?.url ?? p.args?.keyword ?? '')),
                () => runnerInvoke(name, p.args ?? {})
              )
            : await runnerInvoke(name, p.args ?? {})

          await gateway?.request('tool.result', { call_id: p.call_id, result })
        } catch (err) {
          try {
            await gateway?.request('tool.result', {
              call_id: p.call_id,
              result: { ok: false, error: err instanceof Error ? err.message : String(err) }
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

      // 头像身份已变化——精灵相册的锚点已过期
      // （服务端按 avatar_id 过滤行），同时清空本地缓存。
      resetSpriteAlbum()
      resetExpressionAvatars()

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
