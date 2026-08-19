import { getAudioContextCtor } from '@/shared/lib/audio-context-ctor'

let current: HTMLAudioElement | null = null
let currentDone: (() => void) | null = null
let currentListeners: [string, EventListener][] = []
let playGen = 0

function detachListeners(audio: HTMLAudioElement): void {
  for (const [type, fn] of currentListeners) {
    audio.removeEventListener(type, fn)
  }

  currentListeners = []
}

export function stopAudio(): void {
  if (current) {
    current.pause()
    // 释放 dataURL-backed src，让编码后的字节（最差约 256KB）即使在 ended/error
    // 没有触发的情况下也变得不可达。
    current.removeAttribute('src')
    current.load()
    detachListeners(current)
    current = null
  }

  if (currentDone) {
    currentDone()
    currentDone = null
  }

  // 同时标记振幅循环退出，并立即取消待处理的帧。
  amplitudeActive = false

  if (amplitudeRaf !== null) {
    cancelAnimationFrame(amplitudeRaf)
    amplitudeRaf = null
  }

  amplitudeSink?.(0)
}

export function nextGen(): number {
  return ++playGen
}

export function isLatestGen(gen: number): boolean {
  return gen === playGen
}

export async function playDataUrl(dataUrl: string, onDone?: () => void): Promise<boolean> {
  stopAudio()
  const audio = new Audio(dataUrl)
  current = audio

  // 在任何 await 之前就挂好 'ended' / 'error' 监听器，避免测试里的快速
  // `emit('ended')`（或真实的音频结束事件）抢在监听器挂好之前到达。
  let resolvePlayback!: (ok: boolean) => void

  const playbackEnded = new Promise<boolean>(resolve => {
    resolvePlayback = resolve
  })

  // `fired` 让 `fireDone` 幂等：即使有多个来源（监听器、stopAudio、
  // play-failure 分支）都试图结算这个 promise，只有第一次调用生效。
  let fired = false

  const fireDone = (ok: boolean): void => {
    if (fired) {
      return
    }

    fired = true

    if (currentDone === stopDone) {
      currentDone = null
    }

    resolvePlayback(ok)

    if (onDone) {
      onDone()
    }
  }

  // `currentDone` 由 stopAudio() 调用。把 `fireDone` 包成一个 thin closure，
  // 始终上报失败（stop 不算"成功"的播放结束）。
  const stopDone = (): void => fireDone(false)

  currentDone = stopDone

  const endedHandler: EventListener = () => fireDone(true)
  const errorHandler: EventListener = () => fireDone(false)
  audio.addEventListener('ended', endedHandler, { once: true })
  audio.addEventListener('error', errorHandler, { once: true })
  currentListeners = [
    ['ended', endedHandler],
    ['error', errorHandler]
  ]

  // 立即启动播放，再并行接上 analyser。
  //
  // 之前的实现——`await startAmplitudeLoop(audio)` 在 `await audio.play()`
  // 之前——强制所有 TTS 播放等待 AudioContext.resume() 完成（空闲/挂起上下文
  // 需要 50–150 ms，且用户在反馈问题前，渲染进程日志里正在记
  // "power profile -> dormant"，证实上下文已经变冷）。在这段等待时间里
  // audio 元素已经加载好但 `play()` 还没调用，所以每句 TTS 的第一个音节
  // 听感上像被"截掉"，尽管编码出的 MP3 本身在 t=0 就开始。
  //
  // HTMLAudioElement 播放与 Web Audio analyser 路由相互独立——
  // `play()` 不要求 AudioContext 处于 running 状态。所以我们先启动播放，
  // 再异步接上 analyser。口型同步会滞后同样的 50–150 ms，
  // 但嘴部在首帧本来也不会有可见动作，
  // analyser接好后仍然能采集到后续充足的数据。
  const playPromise = audio.play().catch(err => err)
  void startAmplitudeLoop(audio).catch(() => undefined)

  const playResult = await playPromise

  if (playResult instanceof Error) {
    if (current === audio) {
      current = null
    }

    fireDone(false)

    return false
  }

  return await playbackEnded
}

// ── Analyser-driven amplitude for 3D lip sync ─────────────────────────────

let audioCtx: AudioContext | null = null
let analyser: AnalyserNode | null = null
let analyserSource: MediaElementAudioSourceNode | null = null
let amplitudeSink: ((amp: number) => void) | null = null
let amplitudeBuffer: Uint8Array | null = null
let amplitudeRaf: number | null = null
let amplitudeActive = false

/** Subscribe to the live audio amplitude [0..1]. Returns a cleanup fn. */
export function registerAmplitudeSink(fn: ((amp: number) => void) | null): () => void {
  amplitudeSink = fn

  return () => {
    if (amplitudeSink === fn) {
      amplitudeSink = null
    }
  }
}

function ensureAnalyser(): void {
  if (analyser && audioCtx) {
    return
  }

  const Ctor = getAudioContextCtor()

  if (!Ctor) {
    return
  }

  audioCtx = new Ctor()
  analyser = audioCtx.createAnalyser()
  analyser.fftSize = 1024
  amplitudeBuffer = new Uint8Array(new ArrayBuffer(analyser.frequencyBinCount))
}

async function startAmplitudeLoop(audio: HTMLAudioElement): Promise<void> {
  ensureAnalyser()

  const ctx = audioCtx
  const analyserNode = analyser

  if (!ctx || !analyserNode || !amplitudeBuffer) {
    // 不支持 Web Audio——静默跳过口型同步，不要崩。
    return
  }

  if (ctx.state === 'suspended') {
    await ctx.resume().catch(() => undefined)
  }

  if (ctx.state !== 'running') {
    return
  }

  // 每个 audio 元素创建一个 MediaElementSource。跨多次切换复用（例如连续的
  // speak() 调用）会泄漏图节点，并触发 "HTMLMediaElement already connected" 的
  // DOMException。
  try {
    analyserSource?.disconnect()
    analyserSource = ctx.createMediaElementSource(audio)
    analyserSource.connect(analyserNode)
    analyserNode.connect(ctx.destination)
  } catch {
    // 该元素已经被连接（用全新的 Audio() 不应发生，但某些测试环境会复用节点）。
    return
  }

  amplitudeActive = true

  if (amplitudeRaf !== null) {
    cancelAnimationFrame(amplitudeRaf)
    amplitudeRaf = null
  }

  const buf = amplitudeBuffer as Uint8Array<ArrayBuffer>

  const tick = () => {
    if (!amplitudeActive || !analyser) {
      return
    }

    analyser.getByteTimeDomainData(buf)
    // 累加相对 0x80（静音中点）的偏差并归一化。
    let sum = 0

    for (let i = 0; i < buf.length; i++) {
      const dev = buf[i] - 128
      sum += dev < 0 ? -dev : dev
    }

    const avg = sum / buf.length
    // 128 是满幅方波的理论最大值；夹到 1。
    amplitudeSink?.(Math.min(1, avg / 96))
    amplitudeRaf = requestAnimationFrame(tick)
  }

  amplitudeRaf = requestAnimationFrame(tick)
}
