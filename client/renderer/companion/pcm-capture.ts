import { getAudioContextCtor } from '@/shared/lib/audio-context-ctor'

import { VOICE_SAMPLE_RATE } from './voice-protocol'

export interface PcmCapture {
  /** VAD 判定说话开始：吐预滚缓冲并开始上行实时块。 */
  start(): void
  /** VAD 静默断句：冲掉不满一块的尾部（服务端拿到完整收音）。 */
  stop(): void
  close(): void
}

const CHUNK_FRAMES = 1600 // 100ms @ 16kHz

// 上行 PCM 采集：专用 16kHz AudioContext（设备原生率由 Chromium 重采样），
// AudioWorklet 优先（渲染线程转换 + 300ms 预滚）；加载失败降级 ScriptProcessorNode
// （主线程转换、无预滚，功能等价仅瞬态稍差）。
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
      start: () => node.port.postMessage('start'),
      stop: () => node.port.postMessage('stop'),
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
    let active = false
    let pending: number[] = []

    processor.onaudioprocess = e => {
      if (!active) {
        return
      }

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
      start: () => {
        active = true
      },
      stop: () => {
        active = false

        if (pending.length > 0) {
          onChunk(Int16Array.from(pending))
          pending = []
        }
      },
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
