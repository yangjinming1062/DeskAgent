// Companion TTS via the existing `deskagent:media:tts` REST IPC (no Backend
// change). Plays sequentially — a new speak() cuts off the prior utterance so
// rapid question transitions don't stack audio. Resolves when playback ends so
// callers can sequence; rejects/returns false on failure so the UI falls back
// to text-only (plan.md §4.5: TTS unavailable → pure text).

import { $companionVoiceId } from './prefs'

let current: HTMLAudioElement | null = null

export function stopSpeaking(): void {
  if (current) {
    current.pause()
    current = null
  }
}

export async function speak(text: string, voice?: string): Promise<boolean> {
  try {
    const res = await window.deskagent.media.tts({ text, voice: voice ?? $companionVoiceId.get() })
    stopSpeaking()
    current = new Audio(res.dataUrl)
    await current.play()
    await new Promise<void>(resolve => {
      const done = () => resolve()
      current?.addEventListener('ended', done, { once: true })
      current?.addEventListener('error', done, { once: true })
    })

    return true
  } catch {
    stopSpeaking()

    return false
  }
}
