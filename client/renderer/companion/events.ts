import { setModelInfo, setWardrobe, type WardrobeItem } from '@/companion/3d/model-store'
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
import { $effectiveTier, $voiceCallOpen, setSpriteState, type SpriteEmotion } from '@/companion/companion-store'
import { $responseMode } from '@/companion/prefs'
import { speak } from '@/companion/tts'
import { $gateway } from '@/shared/store/gateway'
import type { RpcEvent } from '@/shared/types/deskagent'

import { $devMode, pushDevLog } from './developer-overlay'
import { speakProactive } from './proactive/proactive'
import { findWindowByKeyword, performRitualWalk } from './ritual-walk'

// Pull the full wardrobe list and push it into the model-store. Used by the
// wardrobe.updated event — no request is needed otherwise; the UI triggers
// generate / equip / delete via direct REST and refreshes locally.
async function refreshWardrobe(): Promise<void> {
  try {
    const res = await window.deskagent.api<WardrobeItem[]>({ path: '/api/companion/wardrobe' })
    setWardrobe(res ?? [])
  } catch (err) {
    console.warn('[events] wardrobe refresh failed:', err)
  }
}

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
        setSpriteState('idle', { force: true })
      }

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
      // Affect-only cue — quiet-tier pass-through or idle-triggered reasoning:
      // switch to EMOTIONAL without a bubble or TTS.
      const emotion = (event.payload as { emotion?: string } | undefined)?.emotion

      if (emotion && emotion !== 'neutral' && !$screenLocked.get()) {
        setSpriteState('emotional', { emotion: emotion as SpriteEmotion })
      }

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
      const p = event.payload as { model_id?: number; asset_url?: string; species?: string; error?: string } | undefined

      if (p?.error) {
        console.warn('[events] model.ready error:', p.error)

        break
      }

      setModelInfo({
        id: p?.model_id ?? null,
        asset_url: p?.asset_url ?? null,
        species: p?.species ?? null,
        status: 'succeeded'
      })

      break
    }

    case 'wardrobe.updated': {
      // Backend fires this after a wardrobe item is generated, equipped, or
      // deleted. Re-pull the full list so the equipped atom stays in sync.
      void refreshWardrobe()

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
