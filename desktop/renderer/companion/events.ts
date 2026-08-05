import { $screenLocked } from '@/companion/activity'
import { reportInteractionStat } from '@/companion/activity'
import { resolveAvatarRegeneration } from '@/companion/avatar-regen-store'
import {
  appendAssistantDelta,
  beginAssistantMessage,
  finalizeAssistantMessage,
  setAssistantError,
  setAssistantTool
} from '@/companion/chat-store'
import { applyClipUpdate, type ClipMeta } from '@/companion/clip-store'
import { $effectiveTier, $voiceCallOpen, setSpriteState, type SpriteEmotion } from '@/companion/companion-store'
import { $responseMode } from '@/companion/prefs'
import { speak } from '@/companion/tts'
import { $gateway } from '@/shared/store/gateway'
import type { RpcEvent } from '@/shared/types/deskagent'

import { $devMode, pushDevLog } from './developer-overlay'
import { speakProactive } from './proactive/proactive'
import { findWindowByKeyword, performRitualWalk } from './ritual-walk'

export function handleCompanionEvent(event: RpcEvent): void {
  if ($devMode.get()) {
    pushDevLog(event.type, JSON.stringify(event.payload ?? {}))
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
      const payload = event.payload as { text?: string; affect?: { emotion?: string } } | undefined
      const text = payload?.text ?? ''
      const emotion = payload?.affect?.emotion
      // Suppress render-side cues for quiet users and when the screen is locked.
      const quiet = $effectiveTier.get() === 'quiet'
      const screenLocked = $screenLocked.get()

      finalizeAssistantMessage(payload?.text)

      // "neutral" is the LLM's no-op emotion; treat it like no affect so it doesn't ping a badge.
      const hasEmotion = Boolean(emotion && emotion !== 'neutral')

      if (hasEmotion && !screenLocked) {
        setSpriteState('emotional', { emotion: emotion as SpriteEmotion })
      } else {
        setSpriteState('idle')
      }

      // Speak chat replies in "always voice" mode (plan §4.1); skip during an
      // active voice call or a locked screen. Defer a frame so EMOTIONAL is
      // observable before SPEAKING overwrites it (ARCH §7.5).
      if ($responseMode.get() === 'voice' && text.trim() && !$voiceCallOpen.get() && !screenLocked) {
        const say = () => void speak(text).then(() => setSpriteState('idle'))

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
      // Affect-only cue — quiet-tier pass-through or idle-triggered reasoning:
      // switch to EMOTIONAL without a bubble or TTS.
      const emotion = (event.payload as { emotion?: string } | undefined)?.emotion

      if (emotion && emotion !== 'neutral' && !$screenLocked.get()) {
        setSpriteState('emotional', { emotion: emotion as SpriteEmotion })
      }

      break
    }

    case 'tool.call': {
      const p =
        (event.payload as
          | { status?: string; name?: string; args?: Record<string, unknown>; call_id?: string }
          | undefined) ?? {}

      const runnerInvoke = window.deskagent?.runnerInvoke

      if (p.status === 'complete') {
        setAssistantTool(null)
        setSpriteState('thinking')

        break
      }

      const name = p.name ?? '工具'
      setAssistantTool(name)
      setSpriteState('working')

      // Without a bridge or call_id the sprite stays 'working'; the backend's
      // await_future times out at 300s and surfaces the error.
      if (!p.call_id || !runnerInvoke) {
        break
      }

      // Fire-and-forget the Runner call and post the result so the backend's
      // await_future resolves; tool errors must not bubble into this handler.
      const gateway = $gateway.get()

      void (async () => {
        try {
          const result =
            name === 'system.open_application'
              ? await performRitualWalk(
                  () => findWindowByKeyword(String(p.args?.name ?? '')),
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

    case 'clip.updated': {
      const p = event.payload as
        | {
            scene?: string
            tier?: number
            status?: string
            url?: string | null
            keyframe_url?: string | null
            keyframe_meta?: ClipMeta | null
          }
        | undefined

      if (p?.scene && typeof p.tier === 'number') {
        applyClipUpdate({
          scene: p.scene,
          tier: p.tier,
          status: p.status,
          url: p.url ?? null,
          keyframe_url: p.keyframe_url ?? null,
          keyframe_meta: p.keyframe_meta ?? null
        })
      }

      break
    }

    case 'avatar.regenerated': {
      // Background regeneration result — resolve the pending awaiter by job_id
      // so the portrait can swap without blocking the handler.
      const p = event.payload as { job_id?: string; asset_url?: string; id?: number; error?: string } | undefined

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
      }

      break
    }

    default:
      break
  }
}
