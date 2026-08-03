import { $screenLocked } from '@/companion/activity'
import {
  appendAssistantDelta,
  beginAssistantMessage,
  finalizeAssistantMessage,
  setAssistantError,
  setAssistantTool
} from '@/companion/chat-store'
import { applyClipUpdate, type ClipMeta } from '@/companion/clip-store'
import { $disturbanceTier, $spriteState, setSpriteState, type SpriteEmotion } from '@/companion/companion-store'
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

      // ``neutral`` is the LLM's "no specific emotion" answer and must NOT
      // trigger a transient EMOTIONAL state — it would otherwise fall
      // through to ``pulse + ✨`` (companion-ready.tsx default) and ping a
      // meaningless badge on every text reply. Filter at the boundary so
      // ``neutral`` returns to idle like the no-affect path (P1-5).
      const hasEmotion = payload?.affect?.emotion && payload.affect.emotion !== 'neutral'
      if (hasEmotion) {
        setSpriteState('emotional', { emotion: payload!.affect!.emotion as SpriteEmotion })
      } else {
        setSpriteState('idle')
      }

      // "Always voice" response mode (plan §4.1): speak chat replies aloud.
      // Voice-call mode drives its own speaking in VoiceCallDock and never
      // reaches here with a chat dock open, so this only fires for Chat mode.
      //
      // P1-13: when an emotion is present, defer the speaking state by a
      // short frame so the EMOTIONAL state is observable before being
      // overwritten (ARCH §7.5 "使 Desktop 能在 SPEAKING 前先播 EMOTIONAL").
      // Without this, the synchronous ``setSpriteState('speaking')``
      // immediately cancels the 2.5s emotional transient timer.
      if ($responseMode.get() === 'voice' && payload?.text?.trim()) {
        if (hasEmotion) {
          setTimeout(() => {
            setSpriteState('speaking')
            void speak(payload!.text!).then(() => setSpriteState('idle'))
          }, 1200)
        } else {
          setSpriteState('speaking')
          void speak(payload.text).then(() => setSpriteState('idle'))
        }
      }

      break
    }

    case 'affect': {
      const emotion = (event.payload as { emotion?: string } | undefined)?.emotion

      // ``neutral`` → no state change (see P1-5 note above).
      if (emotion && emotion !== 'neutral') {
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
      // P0 (desktop audit): the previous code unconditionally set
      // the sprite to 'idle' on any error, which clobbered
      // working / speaking / tool-running states. The error
      // message is surfaced via chat-store (setAssistantError);
      // the sprite state only resets when the LLM stream is
      // already idle. Tool / speaking transitions still win
      // because they have higher priority.
      const message = (event.payload as { message?: string } | undefined)?.message ?? '出了点小问题'
      setAssistantError(message)
      if ($spriteState.get() === 'idle' || $spriteState.get() === 'thinking' || $spriteState.get() === 'listening') {
        setSpriteState('idle')
      }

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
      // ``neutral`` is filtered at this boundary too (P1-5) so a
      // system-prompt-driven default doesn't ping a meaningless badge.
      if (affectEmotion && affectEmotion !== 'neutral') {
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
