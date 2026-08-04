import { $companionVoiceId } from './prefs'
import { $voicePreparing } from './voice-state'

// Companion TTS via `deskagent:media:tts` REST IPC.

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
    if (gen === speakGen) {
      $voicePreparing.set(false)
    }

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
    if (gen === speakGen) {
      $voicePreparing.set(false)
    }
  }
}
