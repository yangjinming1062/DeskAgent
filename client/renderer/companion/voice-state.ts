import { atom } from 'nanostores'

// 跨 companion renderer 共享的语音准备状态。
// 用引用计数持有：begin/end 成对调用，只有计数归零时才向订阅者发出 false。
// 直接 .set(true) 会被订阅者误判为「永远在准备」——这是之前 onboarding-audio / tts 的 finally
// 用 isLatestGen 判定被 playDataUrl 内部 bump 的 playGen 污染而失效的根因。

export const $voicePreparing = atom<boolean>(false)

let activeCount = 0

export function beginVoicePreparing(): void {
  activeCount++

  if (activeCount === 1) {
    $voicePreparing.set(true)
  }
}

export function endVoicePreparing(): void {
  if (activeCount === 0) {
    return
  }

  activeCount--

  if (activeCount === 0) {
    $voicePreparing.set(false)
  }
}
