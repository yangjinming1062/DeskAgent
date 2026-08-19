import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { __resetChatPlayButtonStateForTests, ChatMessagePlayButton } from './chat-message-play-button'

// `tts.ts` is mocked via vi.hoisted so the mock factory can reference the same
// vi.fn() instances that the assertions check. Mirrors the pattern in
// `reactions/reaction-audio.test.ts`.
const hoisted = vi.hoisted(() => ({
  speakChatMessage: vi.fn<(text: string, voice?: string, onDone?: () => void) => Promise<boolean>>(),
  stopSpeaking: vi.fn()
}))

vi.mock('./tts', () => ({
  speakChatMessage: hoisted.speakChatMessage,
  stopSpeaking: hoisted.stopSpeaking
}))

function makeDeferred(): { promise: Promise<boolean>; resolve: (v: boolean) => void } {
  let resolve!: (v: boolean) => void

  const promise = new Promise<boolean>(r => {
    resolve = r
  })

  return { promise, resolve }
}

describe('ChatMessagePlayButton', () => {
  beforeEach(() => {
    hoisted.speakChatMessage.mockReset()
    hoisted.stopSpeaking.mockReset()
    hoisted.speakChatMessage.mockResolvedValue(true)
    hoisted.stopSpeaking.mockImplementation(() => undefined)
    __resetChatPlayButtonStateForTests()
  })

  afterEach(() => {
    cleanup()
    __resetChatPlayButtonStateForTests()
  })

  it('renders a play button that is enabled by default', () => {
    render(<ChatMessagePlayButton messageId="m1" text="你好" />)
    const btn = screen.getByRole('button', { name: '播放' })

    expect(btn).toBeDefined()
    expect(btn.hasAttribute('disabled')).toBe(false)
  })

  it('on click, dispatches speakChatMessage with the message text and an onDone callback', async () => {
    const deferred = makeDeferred()
    hoisted.speakChatMessage.mockReturnValue(deferred.promise)

    render(<ChatMessagePlayButton messageId="m1" text="你好呀" />)
    fireEvent.click(screen.getByRole('button', { name: '播放' }))

    expect(hoisted.speakChatMessage).toHaveBeenCalledTimes(1)
    expect(hoisted.speakChatMessage.mock.calls[0][0]).toBe('你好呀')
    // 第二参数 voice 显式传 undefined —— 由 tts.ts 内部回退到 $companionVoiceId。
    expect(hoisted.speakChatMessage.mock.calls[0][1]).toBeUndefined()
    // 第三参数必须是一个 onDone 回调（保证音频结束后能清掉 playing 状态）。
    expect(typeof hoisted.speakChatMessage.mock.calls[0][2]).toBe('function')

    // 模拟音频自然播放完 —— 调用我们之前传给 speakChatMessage 的 onDone，
    // 应清掉 playing 状态，按钮回到 idle。
    const onDone = hoisted.speakChatMessage.mock.calls[0][2]

    expect(onDone).toBeDefined()
    onDone?.()

    deferred.resolve(true)
    await deferred.promise

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '播放' })).toBeDefined()
    })
  })

  it('clicking the same button while playing stops via stopSpeaking instead of a new speak call', async () => {
    // speakChatMessage 永远 pending（模拟音频正在播放、未结束）。
    const never = new Promise<boolean>(() => {})
    hoisted.speakChatMessage.mockReturnValue(never)

    render(<ChatMessagePlayButton messageId="m1" text="你好" />)
    fireEvent.click(screen.getByRole('button', { name: '播放' }))

    // 此刻按钮已切到「停止播放」态。
    expect(screen.getByRole('button', { name: '停止播放' })).toBeDefined()

    fireEvent.click(screen.getByRole('button', { name: '停止播放' }))

    expect(hoisted.stopSpeaking).toHaveBeenCalledTimes(1)
    // 关键断言：再次点击同一个按钮**不会**再触发 speakChatMessage。
    expect(hoisted.speakChatMessage).toHaveBeenCalledTimes(1)
  })

  it('clicking a different message button preempts the previous one and triggers a new speak', async () => {
    const first = makeDeferred()
    const second = makeDeferred()
    hoisted.speakChatMessage.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)

    const { rerender } = render(<ChatMessagePlayButton messageId="m1" text="第一条" />)
    fireEvent.click(screen.getByRole('button', { name: '播放' }))

    // 切换到第二条消息的按钮。注意这里有 React 的重新订阅；新按钮会立即读
    // 到 currentPlayingId === "m1"（来自第一条的 onClick），所以它处于"其他正在播放"
    // 状态，应被禁用 —— 但模拟点击依然能走到 preempt 路径（stopSpeaking 内部清理）。
    rerender(<ChatMessagePlayButton messageId="m2" text="第二条" />)

    // 第一条 onDone 还没被调用，但只要 stopSpeaking 被调用，组件内的同步清理
    // 会通过旧 onDone 完成（见组件 onClick 的「场景 B」分支）。
    // 这里通过手动调用 onDone 来模拟 audio-track.stopAudio 的清理副作用。
    const firstOnDone = hoisted.speakChatMessage.mock.calls[0][2]

    firstOnDone?.()

    fireEvent.click(screen.getByRole('button', { name: '播放' }))

    expect(hoisted.speakChatMessage).toHaveBeenCalledTimes(2)
    expect(hoisted.speakChatMessage.mock.calls[1][0]).toBe('第二条')
    expect(hoisted.speakChatMessage.mock.calls[1][1]).toBeUndefined()
  })

  it('clears state back to idle when speakChatMessage rejects', async () => {
    hoisted.speakChatMessage.mockRejectedValueOnce(new Error('boom'))

    render(<ChatMessagePlayButton messageId="m1" text="x" />)
    fireEvent.click(screen.getByRole('button', { name: '播放' }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '播放' }).hasAttribute('disabled')).toBe(false)
    })
  })
})
