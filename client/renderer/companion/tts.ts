import { isLatestGen, nextGen, playDataUrl, stopAudio } from './audio-track'
import { $companionVoiceId } from './prefs'
import { $voicePreparing } from './voice-state'

// Companion TTS via `spiritagent:media:tts` REST IPC.

export function stopSpeaking(): void {
  stopAudio()
}

async function synth(
  text: string,
  voice: string | undefined,
  context: string | undefined,
  persist: boolean
): Promise<boolean> {
  const gen = nextGen()
  $voicePreparing.set(true)

  try {
    const res = await window.spiritagent.media.tts({
      text,
      voice: voice ?? $companionVoiceId.get(),
      context: context ?? null,
      persist
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

/** 动态台词（聊天回复 / 主动消息 / 语音通话）。只走内存缓存，不落盘。 */
export async function speak(text: string, voice?: string, context?: string): Promise<boolean> {
  return await synth(text, voice, context, false)
}

/** 源码里写死的台词（戳一戳反应、音色试听句）。合成结果按内容寻址落盘，
 *  同一组 (音色, 台词) 一辈子只花一次云端额度。 */
export async function speakScripted(text: string, voice?: string, context?: string): Promise<boolean> {
  return await synth(text, voice, context, true)
}
