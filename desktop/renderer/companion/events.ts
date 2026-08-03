import { $screenLocked } from '@/companion/activity'
import {
  appendAssistantDelta,
  beginAssistantMessage,
  finalizeAssistantMessage,
  setAssistantError,
  setAssistantTool
} from '@/companion/chat-store'
import { applyClipUpdate, type ClipMeta } from '@/companion/clip-store'
import { $disturbanceTier, setSpriteState, type SpriteEmotion } from '@/companion/companion-store'
import { resolveAvatarRegeneration } from '@/companion/avatar-regen-store'
import { $responseMode } from '@/companion/prefs'
import { speak } from '@/companion/tts'
import type { RpcEvent } from '@/shared/types/deskagent'

import { pushDevLog } from './developer-overlay'
import { speakProactive } from './proactive/proactive'

export function handleCompanionEvent(event: RpcEvent): void {
  pushDevLog(event.type, JSON.stringify(event.payload ?? {}))

  switch (event.type) {
    case 'message.start':
      beginAssistantMessage()
      setSpriteState('thinking')

      break
    case 'message.delta': {
      const text = (event.payload as { text?: string } | undefined)?.text ?? ''

      if (text) {appendAssistantDelta(text)}

      break
    }

    case 'message.complete': {
      const payload = event.payload as { text?: string; affect?: { emotion?: string } } | undefined
      finalizeAssistantMessage(payload?.text)

      if (payload?.affect?.emotion) {
        setSpriteState('emotional', { emotion: payload.affect.emotion as SpriteEmotion })
      } else {
        setSpriteState('idle')
      }

      // "Always voice" response mode (plan §4.1): speak chat replies aloud.
      // Voice-call mode drives its own speaking in VoiceCallDock and never
      // reaches here with a chat dock open, so this only fires for Chat mode.
      if ($responseMode.get() === 'voice' && payload?.text?.trim()) {
        setSpriteState('speaking')
        void speak(payload.text).then(() => setSpriteState('idle'))
      }

      break
    }

    case 'affect': {
      const emotion = (event.payload as { emotion?: string } | undefined)?.emotion

      if (emotion) {
        setSpriteState('emotional', { emotion: emotion as SpriteEmotion })
      }

      break
    }

    case 'tool.call': {
      const p = (event.payload as { status?: string; name?: string } | undefined) ?? {}

      if (p.status === 'complete') {
        setAssistantTool(null)
        setSpriteState('thinking')
      } else {
        setAssistantTool(p.name ?? '工具')
        setSpriteState('working')
      }

      break
    }

    case 'clip.updated': {
      const p = event.payload as {
        scene?: string
        tier?: number
        status?: string
        url?: string | null
        keyframe_url?: string | null
        keyframe_meta?: ClipMeta | null
      } | undefined

      if (p?.scene && typeof p.tier === 'number') {
        applyClipUpdate({
          scene: p.scene,
          tier: p.tier,
          status: p.status,
          url: p.url ?? null,
          keyframe_url: p.keyframe_url ?? null,
          keyframe_meta: p.keyframe_meta ?? null,
        })
      }

      break
    }

    case 'avatar.regenerated': {
      // P0-4: avatar.regenerate is now a background task; the handler returns
      // {queued: true, job_id} and the real result lands here. Resolve the
      // pending promise keyed by job_id so the awaiter can swap the portrait.
      const p = event.payload as { job_id?: string; asset_url?: string; id?: number; error?: string } | undefined
      if (p?.job_id) {
        resolveAvatarRegeneration(p)
      }

      break
    }

    case 'error': {
      const message = (event.payload as { message?: string } | undefined)?.message ?? '出了点小问题'
      setAssistantError(message)
      setSpriteState('idle')

      break
    }

    case 'cron.trigger': {
      // P0-5: the autonomous chat turn is now the actual product path (the
      // LLM runs the cron prompt and may call send_message_tool, which
      // produces a companion.message that flows through the normal TTS
      // pipeline). This event is informational — the desktop can use it
      // to show a "scheduled message" indicator before the actual reply
      // arrives, or to log the schedule hit for the developer overlay.
      pushDevLog('cron.trigger', JSON.stringify(event.payload ?? {}))
      break
    }
    case 'companion.message': {
      const payload = event.payload as { text?: string; affect?: { emotion?: string } } | undefined
      const text = payload?.text ?? ''
      const currentTier = $disturbanceTier.get()
      const affectEmotion = payload?.affect?.emotion

      // Affect always flows first — the user can see the companion react
      // even at the quiet tier (plan §4.2: 保持安静断消息不断情绪).
      if (affectEmotion) {
        setSpriteState('emotional', { emotion: affectEmotion as SpriteEmotion })
      }

      // Quiet tier OR screen locked → suppress the proactive text, but the
      // affect above still flows. Screen-lock silence is itself silent —
      // no bubble pops over a locked screen. Normal tier still gets a
      // bubble (handled inside speakProactive, which decides TTS-vs-text).
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
