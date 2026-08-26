import { getAudioContextCtor } from '@/shared/lib/audio-context-ctor'

import { emitExternalAmplitude } from './audio-track'
import { VOICE_ENCODING_PCM, VOICE_SAMPLE_RATE, type VoiceAudioSegment } from './voice-protocol'

// 下行语音段顺序播放：解码按到达顺序串行化（decodeAudioData 异步，乱序调度会颠倒段落），
// AudioBufferSourceNode 以"上次调度终点"前瞻排期实现段间无缝衔接；
// 输出经 AnalyserNode 驱动 registerAmplitudeSink 的口型振幅（与 audio-track 互斥使用——
// 通话面板打开时聊天朗读路径不会并发播放）。stopAll 供打断即刻静音。
export class VoiceSegmentPlayer {
  private ctx: AudioContext | null = null
  private analyser: AnalyserNode | null = null
  private sources = new Set<AudioBufferSourceNode>()
  private scheduledUntil = 0
  private chain: Promise<void> = Promise.resolve()
  private ampBuffer: Uint8Array | null = null
  private ampRaf: number | null = null
  private ampActive = false
  private drainTimer: ReturnType<typeof setTimeout> | null = null

  onAllPlayed: (() => void) | null = null

  get playing(): boolean {
    return this.sources.size > 0
  }

  enqueue(segment: VoiceAudioSegment): void {
    this.chain = this.chain.then(() => this.decodeAndSchedule(segment)).catch(() => undefined)
  }

  stopAll(): void {
    for (const source of this.sources) {
      try {
        source.onended = null
        source.stop()
      } catch {
        /* already stopped */
      }
    }

    this.sources.clear()
    this.scheduledUntil = 0
    this.stopAmplitudeLoop()

    if (this.drainTimer) {
      clearTimeout(this.drainTimer)
      this.drainTimer = null
    }
  }

  close(): void {
    this.stopAll()
    this.chain = Promise.resolve()

    if (this.ctx) {
      void this.ctx.close().catch(() => undefined)
      this.ctx = null
      this.analyser = null
    }
  }

  private async decodeAndSchedule(segment: VoiceAudioSegment): Promise<void> {
    if (!this.ensureGraph() || !this.ctx || !this.analyser) {
      return
    }

    let buffer: AudioBuffer

    try {
      buffer =
        segment.encoding === VOICE_ENCODING_PCM
          ? this.pcmToBuffer(segment)
          : await this.ctx.decodeAudioData(segment.payload)
    } catch {
      // 单段解码失败（含打断竞速的半截帧）跳过，不阻塞后续段。
      return
    }

    if (!this.ctx) {
      return
    }

    const source = this.ctx.createBufferSource()
    source.buffer = buffer
    source.connect(this.analyser)
    const startAt = Math.max(this.scheduledUntil, this.ctx.currentTime)
    source.start(startAt)
    this.scheduledUntil = startAt + buffer.duration
    this.sources.add(source)

    source.onended = () => {
      this.sources.delete(source)

      if (this.sources.size === 0) {
        this.scheduleDrainedCheck()
      }
    }

    this.startAmplitudeLoop()
  }

  private pcmToBuffer(segment: VoiceAudioSegment): AudioBuffer {
    const ints = new Int16Array(segment.payload)
    const rate = segment.sampleRate || VOICE_SAMPLE_RATE
    const buffer = this.ctx!.createBuffer(1, ints.length, rate)
    const channel = buffer.getChannelData(0)

    for (let i = 0; i < ints.length; i++) {
      channel[i] = ints[i] / 32768
    }

    return buffer
  }

  private ensureGraph(): boolean {
    if (!this.ctx) {
      const Ctor = getAudioContextCtor()

      if (!Ctor) {
        return false
      }

      this.ctx = new Ctor()
      this.analyser = this.ctx.createAnalyser()
      this.analyser.fftSize = 1024
      this.analyser.connect(this.ctx.destination)
      this.ampBuffer = new Uint8Array(this.analyser.frequencyBinCount)
    }

    if (this.ctx.state === 'suspended') {
      void this.ctx.resume().catch(() => undefined)
    }

    return true
  }

  // onended 可能在调度终点前的一拍触发；延后确认全部源真正放完再上报放空。
  private scheduleDrainedCheck(): void {
    if (this.drainTimer) {
      clearTimeout(this.drainTimer)
    }

    this.drainTimer = setTimeout(() => {
      this.drainTimer = null

      if (this.sources.size === 0) {
        this.stopAmplitudeLoop()
        this.onAllPlayed?.()
      }
    }, 80)
  }

  private startAmplitudeLoop(): void {
    if (this.ampActive) {
      return
    }

    this.ampActive = true

    const tick = (): void => {
      if (!this.ampActive || !this.analyser || !this.ampBuffer) {
        this.ampActive = false
        this.ampRaf = null

        return
      }

      this.analyser.getByteTimeDomainData(this.ampBuffer as Uint8Array<ArrayBuffer>)
      let sum = 0

      for (let i = 0; i < this.ampBuffer.length; i++) {
        const dev = this.ampBuffer[i] - 128
        sum += dev < 0 ? -dev : dev
      }

      const avg = sum / this.ampBuffer.length
      emitExternalAmplitude(Math.min(1, avg / 96))
      this.ampRaf = requestAnimationFrame(tick)
    }

    this.ampRaf = requestAnimationFrame(tick)
  }

  private stopAmplitudeLoop(): void {
    this.ampActive = false

    if (this.ampRaf !== null) {
      cancelAnimationFrame(this.ampRaf)
      this.ampRaf = null
    }

    emitExternalAmplitude(0)
  }
}
