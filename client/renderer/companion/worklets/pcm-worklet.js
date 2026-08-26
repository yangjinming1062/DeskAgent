// 语音通话上行采集处理器：在 16kHz 专用 AudioContext 的渲染线程上把 Float32 输入
// 转成 s16le PCM 块（100ms/块）postMessage 回主线程。空闲期维持预滚环形缓冲，
// 收到 'start' 时先吐预滚再续实时块——保住发音起始瞬态（VAD 判定晚于张口）。

const FRAMES_PER_CHUNK = 1600 // 100ms @ 16kHz
const PREROLL_CHUNKS = 3 // 300ms 预滚

class SpiritAgentPcmProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this.active = false
    this.buf = new Float32Array(FRAMES_PER_CHUNK)
    this.pos = 0
    this.preroll = []
    this.port.onmessage = e => {
      if (e.data === 'start') {
        this.active = true

        for (const chunk of this.preroll) {
          this.port.postMessage(chunk, [chunk.buffer])
        }

        this.preroll = []
      } else if (e.data === 'stop') {
        this.active = false

        if (this.pos > 0) {
          this.port.postMessage(this.takeChunk(), [])
        }
      }
    }
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

        if (this.active) {
          this.port.postMessage(chunk, [chunk.buffer])
        } else {
          this.preroll.push(chunk)

          if (this.preroll.length > PREROLL_CHUNKS) {
            this.preroll.shift()
          }
        }
      }
    }

    return true
  }
}

registerProcessor('spiritagent-pcm', SpiritAgentPcmProcessor)
