import { $screenLocked } from '@/companion/activity'
import { resolveAvatarRegeneration } from '@/companion/avatar-regen-store'
import {
  appendAssistantDelta,
  beginAssistantMessage,
  finalizeAssistantMessage,
  setAssistantError,
  setAssistantTool
} from '@/companion/chat-store'
import { applyClipUpdate, type ClipMeta } from '@/companion/clip-store'
import { $disturbanceTier, $voiceCallOpen, setSpriteState, type SpriteEmotion } from '@/companion/companion-store'
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
      // Suppress render-side cues for quiet users and when the screen is locked.
      const quiet = $disturbanceTier.get() === 'quiet'
      const screenLocked = $screenLocked.get()

      finalizeAssistantMessage(payload?.text)

      // "neutral" is the LLM's no-op emotion; treat it like no affect so it doesn't ping a badge.
      const hasEmotion = payload?.affect?.emotion && payload.affect.emotion !== 'neutral'

      if (hasEmotion && !quiet && !screenLocked) {
        setSpriteState('emotional', { emotion: payload!.affect!.emotion as SpriteEmotion })
      } else {
        setSpriteState('idle')
      }

      // Speak chat replies in "always voice" mode (plan §4.1). Skip while a voice-call is active
      // (its dock speaks) or the screen is locked. Defer speaking a frame so EMOTIONAL is
      // observable before SPEAKING overwrites it (ARCH §7.5).
      if ($responseMode.get() === 'voice' && payload?.text?.trim() && !$voiceCallOpen.get() && !screenLocked) {
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

    case 'companion.affect': {
      // Affect-only cue from the backend — either send_message_tool's quiet-tier
      // pass-through (§6: 断消息不断 affect) or affect_check's idle-triggered
      // LLM reasoning (§7.6). Switches to EMOTIONAL without a bubble or TTS.
      const emotion = (event.payload as { emotion?: string } | undefined)?.emotion

      // ``neutral`` → no state change (see P1-5 note above).
      if (emotion && emotion !== 'neutral') {
        setSpriteState('emotional', { emotion: emotion as SpriteEmotion })
      }

      break
    }

    case 'tool.call': {
      // P0 (contract re-audit): the previous handler only toggled
      // the sprite state — it never actually invoked the Runner or
      // returned the result to the backend. Backend's
      // ``_dispatch_runner_tool`` (``backend/services/chat/
      // tool_dispatch.py``) awaits ``await_future(user_id, call_id)``
      // with a 300s timeout; without a corresponding
      // ``tool.result`` frame the LLM is stuck for 300s and the
      // partner's "help the user do things" promise is broken for
      // every runner-localized tool (terminal, browser, file ops).
      const p = (event.payload as { status?: string; name?: string; args?: Record<string, unknown>; call_id?: string } | undefined) ?? {}

      if (p.status === 'complete') {
        setAssistantTool(null)
        setSpriteState('thinking')

        break
      }

      const name = p.name ?? '工具'
      setAssistantTool(name)
      setSpriteState('working')

      if (!p.call_id || !window.deskagent?.runnerInvoke) {
        // We don't have the bridge or call_id — leave sprite in
        // 'working' so the user can see the partner is trying.
        // Backend will time out at 300s and surface an error.
        break
      }
      // Fire-and-forget: invoke the Runner, send the result back as
      // a JSON-RPC request so the backend's await_future resolves
      // and the LLM can continue. We use ``void`` + ``.catch`` so
      // tool errors don't bubble into the events handler.
      void (async () => {
        try {
          const result = await window.deskagent.runnerInvoke(name, p.args ?? {})
          await window.deskagent.gateway?.request('tool.result', { call_id: p.call_id, result })
        } catch (err) {
          // Surface as a tool.result error so the LLM gets a
          // structured failure instead of waiting the full 300s
          // for the future to time out.
          try {
            await window.deskagent.gateway?.request('tool.result', {
              call_id: p.call_id,
              result: { ok: false, error: err instanceof Error ? err.message : String(err) },
            })
          } catch {
            /* best effort — backend's 300s fallback will catch it */
          }
        }
      })()

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
      // P0 (desktop audit, second pass): the previous P0 fix used
      // a conditional `if (idle/thinking/listening) setSpriteState('idle')`
      // but the priority gate
      // (``STATE_PRIORITY['idle']=10 < STATE_PRIORITY['thinking']=50``)
      // rejects those calls silently — the sprite was still stuck
      // on 'thinking' after an error. Force the reset so the gate
      // is bypassed; the tool / speaking cases keep their state
      // because we explicitly want to clobber the abandoned LLM
      // stream.
      const message = (event.payload as { message?: string } | undefined)?.message ?? '出了点小问题'
      setAssistantError(message)
      setSpriteState('idle', { force: true })

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
      // even when the text is suppressed.
      // ``neutral`` is filtered at this boundary too (P1-5) so a
      // system-prompt-driven default doesn't ping a meaningless badge.
      if (affectEmotion && affectEmotion !== 'neutral') {
        setSpriteState('emotional', { emotion: affectEmotion as SpriteEmotion })
      }

      // The backend's quiet tier already diverts affect-only cues to
      // ``companion.affect`` (never emitting ``companion.message`` for
      // quiet users — see send_message_tool). This quiet check is
      // defense-in-depth: if a companion.message somehow reaches a quiet
      // user, suppress the text but the affect above already flowed.
      // Screen-lock silence is itself silent — no bubble over a locked screen.
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
