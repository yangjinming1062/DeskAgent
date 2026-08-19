import { isLatestGen, nextGen, playDataUrl, stopAudio } from './audio-track'
import { $companionVoiceId } from './prefs'
import { $voicePreparing } from './voice-state'

// 伙伴 TTS 经 `spiritagent:media:tts` REST IPC 调用。

export function stopSpeaking(): void {
  stopAudio()
}

async function synth(
  text: string,
  voice: string | undefined,
  context: string | undefined,
  persist: boolean,
  onDone?: () => void
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

    return await playDataUrl(res.dataUrl, () => {
      // 只有最新一代的生命周期信号有意义——过期 resolve 仍持有闭包，
      // 但触发的也只是一个无害的 no-op。
      if (isLatestGen(gen) && onDone) {
        onDone()
      }
    })
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

/** 聊天窗口里用户主动点击消息气泡下方的「播放」按钮时的入口。语义与
 *  {@link speak} 一致——动态、单次、命中即停——但永远走磁盘缓存，保证同一段
 *  (voice, text) 跨会话只会消耗一次云端额度。 */
export async function speakChatMessage(text: string, voice?: string, onDone?: () => void): Promise<boolean> {
  return await synth(text, voice, 'chat.replay', true, onDone)
}
