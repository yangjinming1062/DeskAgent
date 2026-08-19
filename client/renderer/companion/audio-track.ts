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
    // Release the dataURL-backed src so the encoded bytes (~256KB worst case)
    // become unreachable even if ended/error never fires.
    current.removeAttribute('src')
    current.load()
    detachListeners(current)
    current = null
  }

  if (currentDone) {
    currentDone()
    currentDone = null
  }

  // Also flag the amplitude loop to bail and immediately cancel pending frame.
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

  // Wire up the 'ended' / 'error' listeners BEFORE any await, so a fast
  // `emit('ended')` from a test (or a real audio-end event) can never race
  // past the point where listeners get attached.
  let resolvePlayback!: (ok: boolean) => void

  const playbackEnded = new Promise<boolean>(resolve => {
    resolvePlayback = resolve
  })

  // `fired` makes `fireDone` idempotent: even if multiple sources (listener,
  // stopAudio, play-failure branch) try to settle the promise, only the first
  // call wins.
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

  // `currentDone` is invoked by stopAudio(). Wrap `fireDone` in a thin closure
  // that always reports failure (stop is never a "successful" playback end).
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

  // Kick off playback immediately, then wire up the analyser in parallel.
  //
  // The previous shape — `await startAmplitudeLoop(audio)` BEFORE
  // `await audio.play()` — forced every TTS playback to wait for
  // AudioContext.resume() to settle (50–150 ms on an idle / suspended context,
  // and the renderer was logging "power profile -> dormant" right before the
  // user reported the issue, confirming the context had gone cold). During
  // that wait the audio element was already loaded but `play()` hadn't been
  // called yet, so the very first syllable of every TTS line felt "cut off"
  // even though the encoded MP3 itself began at t=0.
  //
  // HTMLAudioElement playback and Web Audio analyser routing are independent
  // — `play()` does not require the AudioContext to be running. So we start
  // playback first and connect the analyser asynchronously. Lip-sync lags by
  // the same 50–150 ms, but the mouth doesn't visibly move in the first frame
  // anyway, and the analyser pipeline still has plenty of audio to capture
  // once it's wired up.
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
    // Web Audio unsupported — silently skip lip sync rather than crash.
    return
  }

  if (ctx.state === 'suspended') {
    await ctx.resume().catch(() => undefined)
  }

  if (ctx.state !== 'running') {
    return
  }

  // Create one MediaElementSource per audio element. Reusing across swaps
  // (e.g. back-to-back speak() calls) leaks graph nodes and triggers the
  // "HTMLMediaElement already connected" DOMException.
  try {
    analyserSource?.disconnect()
    analyserSource = ctx.createMediaElementSource(audio)
    analyserSource.connect(analyserNode)
    analyserNode.connect(ctx.destination)
  } catch {
    // The element was already connected (shouldn't happen with a fresh
    // Audio() but some test environments recycle nodes).
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
    // Sum the deviation from 0x80 (silence centre) and normalise.
    let sum = 0

    for (let i = 0; i < buf.length; i++) {
      const dev = buf[i] - 128
      sum += dev < 0 ? -dev : dev
    }

    const avg = sum / buf.length
    // 128 is the theoretical max for a full-scale square wave; clamp to 1.
    amplitudeSink?.(Math.min(1, avg / 96))
    amplitudeRaf = requestAnimationFrame(tick)
  }

  amplitudeRaf = requestAnimationFrame(tick)
}
