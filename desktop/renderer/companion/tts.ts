// Companion TTS via the existing `deskagent:media:tts` REST IPC (no Backend
// change). Plays sequentially — a new speak() cuts off the prior utterance so
// rapid question transitions don't stack audio. Resolves when playback ends so
// callers can sequence; rejects/returns false on failure so the UI falls back
// to text-only (plan.md §4.5: TTS unavailable → pure text).
//
// `context` is an optional short tag the IPC layer forwards to the
// `[tts#N]` dev-terminal trace so the operator can correlate a line of log
// output with the call site (e.g. "onboarding.q2", "proactive.greeting").
//
// Sets `$voicePreparing` while the IPC roundtrip + audio.play() are in flight
// so the onboarding/voice-preview UI can show a "正在准备声音…" hint instead
// of silent waiting. The hint covers both the cloud TTS roundtrip and the
// worst-case local Piper auto-download (5-30s on a first TTS call after
// install when the bundled voice is missing). It clears once playback starts
// — disabling 试听/下一个 would block the user for the full sample line.
//
// `stopSpeaking()` pauses the active audio. The pause event does NOT fire the
// `ended` listener the speak() promise awaits, so without an explicit
// notification the in-flight speak() would hang forever. The `currentDone`
// resolver closes that gap: stopSpeaking resolves any pending promise so
// finally can clear the hint. The `speakGen` counter guards against a new
// speak() (which calls stopSpeaking() internally) clearing an older
// in-flight speak()'s hint prematurely.

import { $companionVoiceId } from './prefs'
import { $voicePreparing } from './voice-state'

let current: HTMLAudioElement | null = null
let currentDone: (() => void) | null = null
let speakGen = 0

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
  const gen = ++speakGen
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

    // Playback has started — clear the "preparing" hint so the user can
    // re-trigger 试听/下一个 without waiting for the audio to finish.
    if (gen === speakGen) {$voicePreparing.set(false)}
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
    // Only the latest speak() generation owns the flag, else a stale one drags the sprite to idle mid-utterance.
    if (gen === speakGen) {$voicePreparing.set(false)}
  }
}
