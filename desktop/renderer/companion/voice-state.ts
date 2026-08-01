// Voice preparation state shared across the companion renderer.
//
// `$voicePreparing` is true while a `speak()` call is in flight — the user
// sees a small "正在准备声音…" hint in onboarding / voice preview instead
// of staring at a silent bubble for the 5-30s it can take to download a
// Piper voice on the first call after install. Cleared once playback starts
// or the call fails.
//
// Scope: the sprite window. The hub / framed tool window does not speak
// through `speak()` and never reads this atom — its TTS calls go through
// the cloud engine only.

import { atom } from 'nanostores'

export const $voicePreparing = atom<boolean>(false)

export function setVoicePreparing(preparing: boolean): void {
  $voicePreparing.set(preparing)
}
