import { atom } from 'nanostores'

// 跨 companion renderer 共享的语音准备状态。

export const $voicePreparing = atom<boolean>(false)
