// Companion TTS via the existing `deskagent:media:tts` REST IPC (no Backend
// change). Plays sequentially — a new speak() cuts off the prior utterance so
// rapid question transitions don't stack audio. Resolves when playback ends so
// callers can sequence; rejects/returns false on failure so the UI falls back
// to text-only (plan.md §4.5: TTS unavailable → pure text).
//
// `context` is an optional short tag the IPC layer forwards to the
// `[tts#N]` dev-terminal trace so the operator can correlate a line of log
// output with the call site (e.g. "onboarding.q2", "proactive.greeting").
// The tag is informational only — the IPC layer sanitizes it (string coerce)
// before logging.
//
// Sets `$voicePreparing` while in flight so the onboarding/voice-preview UI
// can show a "正在准备声音…" hint instead of silent waiting. The hint
// covers both the cloud TTS roundtrip and the worst-case local Piper
// auto-download (5-30s on a first TTS call after install when the bundled
// voice is missing).
//
// `stopSpeaking()` pauses the active audio. The pause event does NOT fire
// the `ended` listener the speak() promise awaits, so without an explicit
// notification the in-flight speak() would hang forever and $voicePreparing
// would stay true. The `currentDone` resolver closes that gap: stopSpeaking
// resolves any pending promise so `finally` can clear the hint.

import { $companionVoiceId } from './prefs'
import { $voicePreparing } from './voice-state'

let current: HTMLAudioElement | null = null
let currentDone: (() => void) | null = null

export function stopSpeaking(): void {
  if (current) {
    current.pause()
    current = null
  }
  if (currentDone) {
    currentDone()
    currentDone = null
  }
}

export async function speak(text: string, voice?: string, context?: string): Promise<boolean> {
  $voicePreparing.set(true)
  try {
    const res = await window.deskagent.media.tts({
      text,
      voice: voice ?? $companionVoiceId.get(),
      context: context ?? null
    })
    stopSpeaking()
    current = new Audio(res.dataUrl)
    await current.play()
    await new Promise<void>(resolve => {
      currentDone = resolve
      const done = () => {
        currentDone = null
        resolve()
      }
      current?.addEventListener('ended', done, { once: true })
      current?.addEventListener('error', done, { once: true })
    })

    return true
  } catch {
    stopSpeaking()

    return false
  } finally {
    $voicePreparing.set(false)
  }
}
