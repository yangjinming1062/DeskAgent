import { $authToken, $baseUrl } from './state/auth-store'

// Single persistent audio graph so successive speak() calls don't pile up
// MediaElementSource/AnalyserNode pairs (each source node, once created, is
// pinned to a single element for its lifetime — we just re-point audio.src).
let currentAudio: HTMLAudioElement | null = null
let audioCtx: AudioContext | null = null
let audioSource: MediaElementAudioSourceNode | null = null
let analyser: AnalyserNode | null = null
let analyserData: Uint8Array | null = null
let pollRAF = 0

let ampCallback: ((amp: number) => void) | null = null

export function setAmplitudeCallback(cb: ((amp: number) => void) | null): void {
  ampCallback = cb
}

function ensureAudioGraph(): AudioContext | null {
  if (audioCtx) return audioCtx
  try {
    audioCtx = new AudioContext()
  } catch {
    return null
  }
  if (!currentAudio) currentAudio = new Audio()
  try {
    if (audioCtx.state === 'suspended') void audioCtx.resume()
    audioSource = audioCtx.createMediaElementSource(currentAudio)
    analyser = audioCtx.createAnalyser()
    analyser.fftSize = 256
    analyserData = new Uint8Array(new ArrayBuffer(analyser.frequencyBinCount))
    audioSource.connect(analyser)
    analyser.connect(audioCtx.destination)
  } catch {
    return null
  }
  return audioCtx
}

export async function speak(text: string, voice?: string): Promise<boolean> {
  stopAudio()
  const token = $authToken.get()
  const base = $baseUrl.get()
  const form = new FormData()
  form.append('text', text)
  if (voice) form.append('voice', voice)

  let resp: Response
  try {
    resp = await fetch(`${base}/api/media/tts`, {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: form
    })
  } catch {
    return false
  }
  if (!resp.ok) return false

  const blob = await resp.blob()
  const url = URL.createObjectURL(blob)

  const ctx = ensureAudioGraph()
  if (!ctx || !currentAudio || !analyser || !analyserData) {
    // No analyser available — still play audio, just no lip sync.
    const a = new Audio(url)
    return new Promise(resolve => {
      a.onended = () => { resolve(true) }
      a.onerror = () => resolve(false)
      void a.play().catch(() => resolve(false))
    })
  }

  currentAudio.onended = null
  currentAudio.onerror = null
  currentAudio.src = url
  void currentAudio.play().catch(() => {})

  // Per-frame amplitude polling — cancelled on audio end (see onended below)
  // and on explicit stopAudio().
  const poll = () => {
    if (!analyser || !analyserData || !ampCallback) return
    analyser.getByteTimeDomainData(analyserData as Uint8Array<ArrayBuffer>)
    let sum = 0
    for (let i = 0; i < analyserData.length; i++) sum += Math.abs(analyserData[i] - 128)
    ampCallback(sum / analyserData.length / 128)
    pollRAF = requestAnimationFrame(poll)
  }
  pollRAF = requestAnimationFrame(poll)

  return new Promise(resolve => {
    if (!currentAudio) return resolve(false)
    currentAudio.onended = () => {
      // Stop the polling loop — otherwise it runs forever calling a stale
      // analyser and writing 0 to the amplitude callback.
      if (pollRAF) { cancelAnimationFrame(pollRAF); pollRAF = 0 }
      URL.revokeObjectURL(url)
      ampCallback?.(0)
      resolve(true)
    }
    currentAudio.onerror = () => {
      if (pollRAF) { cancelAnimationFrame(pollRAF); pollRAF = 0 }
      URL.revokeObjectURL(url)
      ampCallback?.(0)
      resolve(false)
    }
  })
}

export function stopAudio(): void {
  if (pollRAF) { cancelAnimationFrame(pollRAF); pollRAF = 0 }
  if (currentAudio) {
    currentAudio.onended = null
    currentAudio.onerror = null
    currentAudio.pause()
    if (currentAudio.src) URL.revokeObjectURL(currentAudio.src)
    currentAudio.removeAttribute('src')
    currentAudio.load()
  }
  ampCallback?.(0)
}
