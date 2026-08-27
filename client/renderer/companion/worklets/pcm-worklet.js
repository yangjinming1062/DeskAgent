// 语音通话上行采集处理器：在 16kHz 专用 AudioContext 的渲染线程上把 Float32 输入
// 转成 s16le PCM 块（100ms/块）postMessage 回主线程。采集常开——话语起止与断句
// 由服务端 VAD 判定，本处理器无起停门。

const FRAMES_PER_CHUNK = 1600 // 100ms @ 16kHz

class SpiritAgentPcmProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this.buf = new Float32Array(FRAMES_PER_CHUNK)
    this.pos = 0
  }

  takeChunk() {
    const out = new Int16Array(this.pos)

    for (let i = 0; i < this.pos; i++) {
      const s = Math.max(-1, Math.min(1, this.buf[i]))
      out[i] = s < 0 ? s * 0x8000 : s * 0x7fff
    }

    this.pos = 0

    return out
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0]

    if (!channel) {
      return true
    }

    let i = 0

    while (i < channel.length) {
      const n = Math.min(channel.length - i, FRAMES_PER_CHUNK - this.pos)
      this.buf.set(channel.subarray(i, i + n), this.pos)
      this.pos += n
      i += n

      if (this.pos === FRAMES_PER_CHUNK) {
        const chunk = this.takeChunk()
        this.port.postMessage(chunk, [chunk.buffer])
      }
    }

    return true
  }
}

registerProcessor('spiritagent-pcm', SpiritAgentPcmProcessor)
