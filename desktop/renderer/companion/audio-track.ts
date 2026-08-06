// Single-track audio shared by runtime TTS and pre-rendered onboarding clips.

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

  try {
    await audio.play()
  } catch {
    if (current === audio) {current = null}

    return false
  }

  await new Promise<void>((resolve) => {
    currentDone = resolve

    const done: EventListener = () => {
      if (currentDone === resolve) {currentDone = null}
      resolve()
    }

    audio.addEventListener('ended', done, { once: true })
    audio.addEventListener('error', done, { once: true })
    currentListeners = [['ended', done], ['error', done]]
  })

  return true
}