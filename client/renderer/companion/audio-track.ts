// Single-track audio shared by runtime TTS and pre-rendered onboarding clips.
//
// Also routes the audio through a Web Audio AnalyserNode so the 3D engine can
// drive lip sync (jawOpen morph) from the live waveform amplitude. The
// analyser is created lazily on first use — some renderers block
// AudioContext construction until a user gesture, so we defer until playDataUrl.

// ── Playback state ────────────────────────────────────────────────────────
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

  // Also flag the amplitude loop to bail so it doesn't try to read from a
  // detached AnalyserNode on the next frame.
  amplitudeActive = false
  amplitudeSink?.(0)
}

export function nextGen(): number {
  return ++playGen
}

export function isLatestGen(gen: number): boolean {
  return gen === playGen
}

export async function playDataUrl(dataUrl: string): Promise<boolean> {
  stopAudio()
  const audio = new Audio(dataUrl)
  current = audio
  // Re-arm the amplitude loop for the new track.
  startAmplitudeLoop(audio)

  try {
    await audio.play()
  } catch {
    if (current === audio) {
      current = null
    }

    return false
  }

  await new Promise<void>(resolve => {
    currentDone = resolve

    const done: EventListener = () => {
      if (currentDone === resolve) {
        currentDone = null
      }

      resolve()
    }

    audio.addEventListener('ended', done, { once: true })
    audio.addEventListener('error', done, { once: true })
    currentListeners = [
      ['ended', done],
      ['error', done]
    ]
  })

  return true
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

  const Ctor =
    window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext

  if (!Ctor) {
    return
  }

  audioCtx = new Ctor()
  analyser = audioCtx.createAnalyser()
  analyser.fftSize = 1024
  amplitudeBuffer = new Uint8Array(new ArrayBuffer(analyser.frequencyBinCount))
}

function startAmplitudeLoop(audio: HTMLAudioElement): void {
  ensureAnalyser()

  if (!audioCtx || !analyser || !amplitudeBuffer) {
    // Web Audio unsupported — silently skip lip sync rather than crash.
    return
  }

  // Create one MediaElementSource per audio element. Reusing across swaps
  // (e.g. back-to-back speak() calls) leaks graph nodes and triggers the
  // "HTMLMediaElement already connected" DOMException.
  try {
    if (audioCtx.state === 'suspended') {
      void audioCtx.resume()
    }

    analyserSource?.disconnect()
    analyserSource = audioCtx.createMediaElementSource(audio)
    analyserSource.connect(analyser)
    analyser.connect(audioCtx.destination)
  } catch {
    // The element was already connected (shouldn't happen with a fresh
    // Audio() but some test environments recycle nodes).
    return
  }

  amplitudeActive = true
  cancelAnimationFrame(amplitudeRaf ?? 0)
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

// stopAudio() flips amplitudeActive off via the next stop; ensure the loop
// bails when the audio element unloads mid-track.
export function isAudioActive(): boolean {
  return amplitudeActive && current !== null
}
