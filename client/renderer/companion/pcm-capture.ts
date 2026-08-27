import { getAudioContextCtor } from '@/shared/lib/audio-context-ctor'

import { VOICE_SAMPLE_RATE } from './voice-protocol'

export interface PcmCapture {
  close(): void
}

const CHUNK_FRAMES = 1600 // 100ms @ 16kHz

// 上行 PCM 采集（常开，全双工）：专用 16kHz AudioContext（设备原生率由 Chromium 重采样），
// AudioWorklet 优先（渲染线程转换）；加载失败降级 ScriptProcessorNode（主线程转换，功能等价）。
// 话语起止与断句由服务端 VAD 判定，采集侧无起停门。
export async function createPcmCapture(stream: MediaStream, onChunk: (pcm: Int16Array) => void): Promise<PcmCapture> {
  const Ctor = getAudioContextCtor()

  if (!Ctor) {
    throw new Error('Web Audio unavailable')
  }

  const ctx = new Ctor({ sampleRate: VOICE_SAMPLE_RATE })
  const source = ctx.createMediaStreamSource(stream)

  try {
    await ctx.audioWorklet.addModule(new URL('./worklets/pcm-worklet.js', import.meta.url).href)
    const node = new AudioWorkletNode(ctx, 'spiritagent-pcm')

    node.port.onmessage = e => {
      if (e.data instanceof Int16Array) {
        onChunk(e.data)
      }
    }

    // 输出经零增益接地，保持节点被音频图持续拉取（不外放、不回声）。
    const mute = ctx.createGain()
    mute.gain.value = 0
    source.connect(node)
    node.connect(mute)
    mute.connect(ctx.destination)

    return {
      close: () => {
        try {
          source.disconnect()
          node.disconnect()
          mute.disconnect()
        } catch {
          /* already torn down */
        }

        void ctx.close().catch(() => undefined)
      }
    }
  } catch {
    const processor = ctx.createScriptProcessor(2048, 1, 1)
    let pending: number[] = []

    processor.onaudioprocess = e => {
      const channel = e.inputBuffer.getChannelData(0)

      for (let i = 0; i < channel.length; i++) {
        const s = Math.max(-1, Math.min(1, channel[i]))
        pending.push(s < 0 ? s * 0x8000 : s * 0x7fff)
      }

      while (pending.length >= CHUNK_FRAMES) {
        onChunk(Int16Array.from(pending.slice(0, CHUNK_FRAMES)))
        pending = pending.slice(CHUNK_FRAMES)
      }
    }

    const mute = ctx.createGain()
    mute.gain.value = 0
    source.connect(processor)
    processor.connect(mute)
    mute.connect(ctx.destination)

    return {
      close: () => {
        processor.onaudioprocess = null

        try {
          source.disconnect()
          processor.disconnect()
          mute.disconnect()
        } catch {
          /* already torn down */
        }

        void ctx.close().catch(() => undefined)
      }
    }
  }
}
