import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $spriteEmotion } from '@/companion/companion-store'

import {
  $expressionAvatar,
  clearExpressionAvatar,
  requestExpressionAvatar,
  resetExpressionAvatars
} from './expression-avatar-store'

const avatarResponse = (
  hash: string,
  name: string
): { id: number; url: string; tag: string; content_hash: string; generated: boolean } => ({
  id: 1,
  url: `http://localhost:8000/api/companion/asset/1/expr_${hash}.png?expires=1&sig=1`,
  tag: name,
  content_hash: hash,
  generated: true
})

interface SpiritagentMock {
  api: ReturnType<typeof vi.fn>
  apiAsset: ReturnType<typeof vi.fn>
}

function setWindowSpiritagent(mock: SpiritagentMock): void {
  ;(window as { spiritagent?: unknown }).spiritagent = { api: mock.api, apiAsset: mock.apiAsset }
}

function restoreWindowSpiritagent(): void {
  delete (window as { spiritagent?: unknown }).spiritagent
}

describe('expression-avatar-store', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-18T12:00:00Z').getTime())
    resetExpressionAvatars()
    $spriteEmotion.set(null)
  })

  afterEach(() => {
    vi.useRealTimers()
    restoreWindowSpiritagent()
    $spriteEmotion.set(null)
  })

  it('publishes the resolved avatar and caches by name', async () => {
    const mock: SpiritagentMock = {
      api: vi.fn().mockResolvedValue(avatarResponse('h1', 'happy')),
      apiAsset: vi.fn().mockResolvedValue('data:image/png;base64,AAA')
    }

    setWindowSpiritagent(mock)
    $spriteEmotion.set('happy')

    await requestExpressionAvatar('happy')
    expect($expressionAvatar.get()).toEqual({ name: 'happy', dataUrl: 'data:image/png;base64,AAA' })

    // Cached switch-back: instant, no refetch.
    $expressionAvatar.set(null)
    await requestExpressionAvatar('happy')
    expect($expressionAvatar.get()?.name).toBe('happy')
    expect(mock.api).toHaveBeenCalledTimes(1)
  })

  it('never requests neutral or empty names', async () => {
    const mock: SpiritagentMock = { api: vi.fn(), apiAsset: vi.fn() }

    setWindowSpiritagent(mock)
    await requestExpressionAvatar('neutral')
    await requestExpressionAvatar('  ')

    expect(mock.api).not.toHaveBeenCalled()
  })

  it('keeps a slow generation cached when it outlives the emotion', async () => {
    let resolveApi: ((v: unknown) => void) | undefined

    const mock: SpiritagentMock = {
      api: vi.fn().mockImplementation(() => new Promise(resolve => (resolveApi = resolve))),
      apiAsset: vi.fn().mockResolvedValue('data:AAA')
    }

    setWindowSpiritagent(mock)
    $spriteEmotion.set('happy')
    const pending = requestExpressionAvatar('happy')

    // Emotional transient ends before the backend answers — the display
    // stays on the portrait, but the result is kept, never wasted.
    $spriteEmotion.set(null)
    resolveApi?.(avatarResponse('h2', 'happy'))
    await pending

    expect($expressionAvatar.get()).toBeNull()

    // Next activation of the same emotion hits the cache: instant swap, no second POST.
    $spriteEmotion.set('happy')
    await requestExpressionAvatar('happy')
    expect($expressionAvatar.get()).toEqual({ name: 'happy', dataUrl: 'data:AAA' })
    expect(mock.api).toHaveBeenCalledTimes(1)
  })

  it('keeps the portrait fallback on failure and backs off for 60s', async () => {
    const mock: SpiritagentMock = {
      api: vi.fn().mockRejectedValueOnce(new Error('down')).mockResolvedValue(avatarResponse('h3', 'sad')),
      apiAsset: vi.fn().mockResolvedValue('data:SAD')
    }

    setWindowSpiritagent(mock)
    $spriteEmotion.set('sad')
    await requestExpressionAvatar('sad')
    expect($expressionAvatar.get()).toBeNull()

    // Within the backoff window the request is skipped entirely.
    await requestExpressionAvatar('sad')
    expect(mock.api).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(61_000)
    await requestExpressionAvatar('sad')
    expect(mock.api).toHaveBeenCalledTimes(2)
    expect($expressionAvatar.get()).toEqual({ name: 'sad', dataUrl: 'data:SAD' })
  })

  it('dedupes concurrent identical requests into one POST', async () => {
    const mock: SpiritagentMock = {
      api: vi.fn().mockResolvedValue(avatarResponse('h4', 'happy')),
      apiAsset: vi.fn().mockResolvedValue('data:AAA')
    }

    setWindowSpiritagent(mock)
    $spriteEmotion.set('happy')
    await Promise.all([requestExpressionAvatar('happy'), requestExpressionAvatar('happy')])
    expect(mock.api).toHaveBeenCalledTimes(1)
  })

  it('clearExpressionAvatar drops the display but keeps caches', async () => {
    const mock: SpiritagentMock = {
      api: vi.fn().mockResolvedValue(avatarResponse('h5', 'happy')),
      apiAsset: vi.fn().mockResolvedValue('data:AAA')
    }

    setWindowSpiritagent(mock)
    $spriteEmotion.set('happy')
    await requestExpressionAvatar('happy')
    clearExpressionAvatar()

    expect($expressionAvatar.get()).toBeNull()

    await requestExpressionAvatar('happy')
    expect($expressionAvatar.get()?.name).toBe('happy')
    expect(mock.api).toHaveBeenCalledTimes(1)
  })

  it('resetExpressionAvatars drops caches so identical names re-fetch', async () => {
    const mock: SpiritagentMock = {
      api: vi.fn().mockResolvedValue(avatarResponse('h6', 'happy')),
      apiAsset: vi.fn().mockResolvedValue('data:AAA')
    }

    setWindowSpiritagent(mock)
    $spriteEmotion.set('happy')
    await requestExpressionAvatar('happy')
    resetExpressionAvatars()

    expect($expressionAvatar.get()).toBeNull()

    await requestExpressionAvatar('happy')
    expect(mock.api).toHaveBeenCalledTimes(2)
  })

  it('an in-flight result from before a reset does not repopulate the cache', async () => {
    let resolveApi: ((v: unknown) => void) | undefined

    const mock: SpiritagentMock = {
      api: vi
        .fn()
        .mockImplementationOnce(() => new Promise(resolve => (resolveApi = resolve)))
        .mockResolvedValue(avatarResponse('h7', 'happy')),
      apiAsset: vi.fn().mockResolvedValue('data:FRESH')
    }

    setWindowSpiritagent(mock)
    $spriteEmotion.set('happy')
    const pending = requestExpressionAvatar('happy')

    // avatar.regenerated lands mid-generation — the in-flight image belongs
    // to the discarded identity and must not re-enter the cache on arrival.
    resetExpressionAvatars()
    resolveApi?.(avatarResponse('h7', 'happy'))
    await pending

    expect($expressionAvatar.get()).toBeNull()

    // The next activation must re-fetch (the stale result was not cached) and
    // publish the fresh-identity image.
    $spriteEmotion.set('happy')
    await requestExpressionAvatar('happy')
    expect(mock.api).toHaveBeenCalledTimes(2)
    expect($expressionAvatar.get()?.dataUrl).toBe('data:FRESH')
  })
})
