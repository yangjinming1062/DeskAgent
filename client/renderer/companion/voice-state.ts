import { atom } from 'nanostores'

// Voice preparation state shared across companion renderer.

export const $voicePreparing = atom<boolean>(false)
