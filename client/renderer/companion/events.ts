import {
  $modelGenError,
  $modelGenProgress,
  $modelGenState,
  hydrateWardrobe,
  setModelInfo
} from '@/companion/3d/model-store'
import { $screenLocked } from '@/companion/activity'
import { reportInteractionStat } from '@/companion/activity'
import { resolveAvatarRegeneration } from '@/companion/avatar-regen-store'
import {
  $chatOpen,
  $chatSessionId,
  appendAssistantDelta,
  beginAssistantMessage,
  finalizeAssistantMessage,
  pushProactiveMessage,
  setAssistantError,
  setAssistantTool
} from '@/companion/chat-store'
import { $effectiveTier, $voiceCallOpen, setSpriteState, type SpriteEmotion } from '@/companion/companion-store'
import { $responseMode } from '@/companion/prefs'
import { computePerchPosition, setLocale, startRoam } from '@/companion/spatial'
import { speak } from '@/companion/tts'
import { log } from '@/shared/lib/log'
import { sleep } from '@/shared/lib/utils'
import { $gateway } from '@/shared/store/gateway'
import type { RpcEvent } from '@/shared/types/deskagent'

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

  // Don't yank the sprite away from an open chat dock.
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

  // Chat-turn events (message.start/delta/complete, tool.*, error) carry the
  // emitting conversation's session_id. Those from a conversation the renderer
  // isn't currently viewing must not be applied to the visible chat — e.g.
  // cron's autonomous turn streams text via the cron conversation; without
  // this gate the user would see cron's reply as if it answered their last
  // main-session message. WSEvent-driven events (companion.message/affect,
  // wardrobe.*, model.*, avatar.regenerated, reload.mcp) have no session_id
  // and pass through.
  if (event.session_id !== undefined) {
    const current = $chatSessionId.get()

    if (current === null || event.session_id !== current) {
      return
    }
  }

  switch (event.type) {
    case 'message.start':
      beginAssistantMessage()
      setSpriteState('thinking')

      break
    case 'message.delta': {
      const text = (event.payload as { text?: string } | undefined)?.text ?? ''

      if (text) {
        appendAssistantDelta(text)
      }

      break
    }

    case 'message.complete': {
      const payload = event.payload as
        | { text?: string; affect?: { emotion?: string; locale?: string; target?: string } }
        | undefined

      const text = payload?.text ?? ''
      const emotion = payload?.affect?.emotion
      const locale = payload?.affect?.locale
      const target = payload?.affect?.target

      // Suppress render-side cues for quiet users and when the screen is locked.
      const quiet = $effectiveTier.get() === 'quiet'
      const screenLocked = $screenLocked.get()

      finalizeAssistantMessage(payload?.text)

      // "neutral" is the LLM's no-op emotion; treat it like no affect so it doesn't ping a badge.
      const hasEmotion = Boolean(emotion && emotion !== 'neutral')

      if (hasEmotion && !screenLocked) {
        setSpriteState('emotional', { emotion: emotion as SpriteEmotion })
      } else {
        setSpriteState('idle', { force: true })
      }

      applySpatialCue(locale, target)

      // Speak chat replies in "always voice" mode (plan §4.1); skip during an
      // active voice call or a locked screen. Defer a frame so EMOTIONAL is
      // observable before SPEAKING overwrites it (ARCH §7.5).
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

      // Daily interaction stats — chat_turn counts only when there's actual
      // text to count (matches the TTS gate above). The shared helper in
      // activity.ts owns the fire-and-forget RPC.
      if (text.trim()) {
        reportInteractionStat('chat_turn')
      }

      break
    }

    case 'companion.affect': {
      // Affect & spatial embodied cue from LLM or backend:
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
      // Universal WORKING entry — tool_start is emitted for ALL tools (backend,
      // memory, runner) before execution begins, so the sprite enters WORKING
      // regardless of tool location. tool.call (below) only fires for runner
      // tools and carries the args for IPC dispatch.
      const p = event.payload as { name?: string } | undefined

      setAssistantTool(p?.name ?? '工具')
      setSpriteState('working')

      break
    }

    case 'tool.call': {
      // Runner dispatch only — WORKING was already set by tool.start.
      // tool.call carries the args needed for runner IPC; without a bridge or
      // call_id the backend's await_future times out at 300s and surfaces the error.
      const p = (event.payload as { name?: string; args?: Record<string, unknown>; call_id?: string } | undefined) ?? {}

      const runnerInvoke = window.deskagent?.runnerInvoke

      if (!p.call_id || !runnerInvoke) {
        break
      }

      const name = p.name ?? ''

      // Fire-and-forget the Runner call and post the result so the backend's
      // await_future resolves; tool errors must not bubble into this handler.
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
            /* best effort — backend's 300s fallback covers it */
          }
        }
      })()

      break
    }

    case 'tool.complete': {
      // Universal WORKING exit — tool_end is emitted for ALL tools in the
      // finally block. force: THINKING (50) < WORKING (70), so the priority
      // gate would silently reject the transition without it.
      setAssistantTool(null)
      setSpriteState('thinking', { force: true })

      break
    }

    case 'model.ready': {
      // Backend pushes this after a /api/companion/model generation finishes.
      // The 3D engine reloads whenever $modelInfo.asset_url changes (see
      // companion-3d.tsx). error field surfaces generation failures; the UI
      // logs it for now — recovery flow is a later slice.
      const p = event.payload as
        | { model_id?: number; asset_url?: string; species?: string; rig_type?: string; error?: string }
        | undefined

      if (p?.error) {
        log.warn('events', 'model.ready error:', p.error)
        $modelGenState.set('failed')
        $modelGenError.set(p.error)
        $modelGenProgress.set(null)

        break
      }

      $modelGenState.set('succeeded')
      $modelGenProgress.set(null)
      $modelGenError.set(null)
      setModelInfo({
        id: p?.model_id ?? null,
        asset_url: p?.asset_url ?? null,
        species: p?.species ?? null,
        rig_type: p?.rig_type ?? 'biped',
        status: 'succeeded'
      })

      break
    }

    case 'model.gen.progress': {
      const p = event.payload as { stage?: string; progress?: number } | undefined
      $modelGenState.set('generating')
      $modelGenProgress.set({ stage: p?.stage ?? '', progress: p?.progress ?? 0 })

      break
    }

    case 'model.failed': {
      const p = event.payload as { reason?: string } | undefined
      $modelGenState.set('failed')
      $modelGenError.set(p?.reason ?? '3D 模型生成失败')
      $modelGenProgress.set(null)

      break
    }

    case 'wardrobe.updated': {
      // Backend fires this after a wardrobe item is generated, equipped, or
      // deleted. Re-pull the full list so the equipped atom stays in sync.
      void hydrateWardrobe()

      break
    }

    case 'wardrobe.gift': {
      // Companion generated a costume gift during Stage 5 autonomous creation.
      void hydrateWardrobe()
      const p = event.payload as { name?: string; message?: string; reason?: string } | undefined

      const msg =
        p?.message || (p?.name ? `为你准备了一份装扮礼物「${p.name}」，快去装扮屋拆开看看吧！` : '为你准备了一份礼物！')

      void speakProactive(msg, { affect: 'excited' })

      break
    }

    case 'avatar.regenerated': {
      // Background regeneration result — resolve the pending awaiter by job_id
      // so the portrait can swap without blocking the handler.
      const p = event.payload as
        | {
            job_id?: string
            asset_url?: string | null
            seed_front_url?: string | null
            seed_right_url?: string | null
            seed_back_url?: string | null
            id?: number
            error?: string
          }
        | undefined

      if (p?.job_id) {
        resolveAvatarRegeneration(p)
      }

      break
    }

    case 'error': {
      // Force the idle reset — the priority gate silently rejects a plain
      // transition while the sprite is on 'thinking'/'working'.
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

      // Affect flows before text so the reaction shows even when text is suppressed.
      if (affectEmotion && affectEmotion !== 'neutral') {
        setSpriteState('emotional', { emotion: affectEmotion as SpriteEmotion })
      }

      // Quiet tier and locked screen suppress the bubble; the affect above still flows.
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
