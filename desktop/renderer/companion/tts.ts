import { isLatestGen, nextGen, playDataUrl, stopAudio } from './audio-track'
import { $companionVoiceId } from './prefs'
import { $voicePreparing } from './voice-state'

// Companion TTS via `deskagent:media:tts` REST IPC.

export function stopSpeaking(): void {
  stopAudio()
}

export async function speak(text: string, voice?: string, context?: string): Promise<boolean> {
  const gen = nextGen()
  $voicePreparing.set(true)

  try {
    const res = await window.deskagent.media.tts({
      text,
      voice: voice ?? $companionVoiceId.get(),
      context: context ?? null
    })

    if (!isLatestGen(gen)) {
      return false
    }

    return await playDataUrl(res.dataUrl)
  } catch {
    stopAudio()

    return false
  } finally {
    if (isLatestGen(gen)) {
      $voicePreparing.set(false)
    }
  }
}