// Returns the AudioContext constructor available on `window`, falling back to
// Safari's prefixed `webkitAudioContext`. Returns `null` if neither exists —
// caller skips audio analysis / recording rather than crashing.

export function getAudioContextCtor(): typeof AudioContext | null {
  if (typeof window === 'undefined') {
    return null
  }

  const Window = window as unknown as { webkitAudioContext?: typeof AudioContext }

  return window.AudioContext ?? Window.webkitAudioContext ?? null
}
