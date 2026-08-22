import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $companionLifecycle, type CompanionLifecycle, type SpriteStateName } from '@/companion/companion-store'

import { $modelInfo } from '../3d/model-store'

import { semanticRequestFor, WAITING_REQUEST } from './sprite-semantics'
import { $activeSprite, $glbLoadFailed, $staticMode, requestSprite, resetSpriteAlbum } from './sprite-store'

const spriteResponse = (
  hash: string,
  tag: string
): { id: number; url: string; tag: string; content_hash: string; generated: boolean } => ({
  id: 1,
  url: `http://localhost:8000/api/companion/asset/1/sprite_${hash}.png?expires=1&sig=1`,
  tag,
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

function setModel(assetUrl: string | null, status: string): void {
  $modelInfo.set({ ...$modelInfo.get(), asset_url: assetUrl, status })
}

describe('sprite-store', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    // 真实尺度的 epoch：store 初始的 _lastPostAt=0 哨兵值不应被误判为「刚提交过」
    // （0 时刻的时钟会阻塞第一次请求）。
    vi.setSystemTime(new Date('2026-08-14T12:00:00Z').getTime())
    resetSpriteAlbum()
    $glbLoadFailed.set(false)
    setModel(null, 'pending')
    $companionLifecycle.set('ready')
  })

  afterEach(() => {
    vi.useRealTimers()
    restoreWindowSpiritagent()
  })

  it('publishes the resolved sprite and caches by content_hash', async () => {
    const mock: SpiritagentMock = {
      api: vi.fn().mockResolvedValue(spriteResponse('h1', '等待')),
      apiAsset: vi.fn().mockResolvedValue('data:image/png;base64,AAA')
    }

    setWindowSpiritagent(mock)

    await requestSprite(WAITING_REQUEST, 'waiting')
    expect($activeSprite.get()).toEqual({ dataUrl: 'data:image/png;base64,AAA', tag: '等待' })

    // 第二次请求解析到相同 hash（相册命中）时不应再拉资源。
    mock.api.mockResolvedValue(spriteResponse('h1', '等待'))
    await requestSprite('另一种语义请求')
    expect(mock.apiAsset).toHaveBeenCalledTimes(1)
  })

  it('dedupes concurrent identical requests into one POST', async () => {
    const mock: SpiritagentMock = {
      api: vi.fn().mockResolvedValue(spriteResponse('h2', '等待')),
      apiAsset: vi.fn().mockResolvedValue('data:AAA')
    }

    setWindowSpiritagent(mock)

    await Promise.all([requestSprite('同一个请求'), requestSprite('同一个请求')])
    expect(mock.api).toHaveBeenCalledTimes(1)
  })

  it('keeps the current image when the backend fails', async () => {
    const mock: SpiritagentMock = {
      api: vi.fn().mockResolvedValueOnce(spriteResponse('h3', '等待')).mockRejectedValueOnce(new Error('down')),
      apiAsset: vi.fn().mockResolvedValue('data:AAA')
    }

    setWindowSpiritagent(mock)

    await requestSprite(WAITING_REQUEST, 'waiting')
    const before = $activeSprite.get()

    await requestSprite('会失败的请求')
    expect($activeSprite.get()).toBe(before)
  })

  it('switches immediately between cached requests without re-fetching', async () => {
    const mock: SpiritagentMock = {
      api: vi
        .fn()
        .mockResolvedValueOnce(spriteResponse('h_idle', '站立'))
        .mockResolvedValueOnce(spriteResponse('h_alt', '回应')),
      apiAsset: vi
        .fn()
        .mockResolvedValueOnce('data:image/png;base64,IDLE')
        .mockResolvedValueOnce('data:image/png;base64,ALT')
    }

    setWindowSpiritagent(mock)

    await requestSprite('站立请求')
    expect($activeSprite.get()).toEqual({ dataUrl: 'data:image/png;base64,IDLE', tag: '站立' })

    vi.advanceTimersByTime(1500)
    await requestSprite('回应请求')
    expect($activeSprite.get()).toEqual({ dataUrl: 'data:image/png;base64,ALT', tag: '回应' })

    // 再次请求已经解析过的 idle 请求时，立刻切换当前精灵
    await requestSprite('站立请求')
    expect($activeSprite.get()).toEqual({ dataUrl: 'data:image/png;base64,IDLE', tag: '站立' })
    expect(mock.api).toHaveBeenCalledTimes(2)
  })

  it('spaces distinct requests and executes trailing request after throttle', async () => {
    const mock: SpiritagentMock = {
      api: vi
        .fn()
        .mockResolvedValueOnce(spriteResponse('h4_a', '等待'))
        .mockResolvedValueOnce(spriteResponse('h4_b', '睡觉')),
      apiAsset: vi.fn().mockResolvedValueOnce('data:A').mockResolvedValueOnce('data:B')
    }

    setWindowSpiritagent(mock)

    await requestSprite('第一个请求')
    expect($activeSprite.get()).toEqual({ dataUrl: 'data:A', tag: '等待' })

    // 第二次请求落在 1.5s 节流窗口内——作为最新 pending 入队
    await requestSprite('第二个请求')
    expect(mock.api).toHaveBeenCalledTimes(1)

    // 推进时间，让 trailing 定时器触发
    await vi.advanceTimersByTimeAsync(1500)
    expect(mock.api).toHaveBeenCalledTimes(2)
    expect($activeSprite.get()).toEqual({ dataUrl: 'data:B', tag: '睡觉' })
  })

  it('resetSpriteAlbum drops caches so identical requests re-fetch', async () => {
    const mock: SpiritagentMock = {
      api: vi.fn().mockResolvedValue(spriteResponse('h5', '等待')),
      apiAsset: vi.fn().mockResolvedValue('data:AAA')
    }

    setWindowSpiritagent(mock)

    await requestSprite(WAITING_REQUEST, 'waiting')
    resetSpriteAlbum()

    expect($activeSprite.get()).toBeNull()

    await requestSprite(WAITING_REQUEST, 'waiting')
    expect(mock.api).toHaveBeenCalledTimes(2)
  })
})

describe('$staticMode', () => {
  afterEach(() => {
    $glbLoadFailed.set(false)
    setModel(null, 'pending')
    $companionLifecycle.set('ready')
  })

  const cases: Array<{
    lifecycle: CompanionLifecycle
    url: string | null
    status: string
    failed: boolean
    expected: boolean
    label: string
  }> = [
    {
      lifecycle: 'unauthed',
      url: null,
      status: 'pending',
      failed: false,
      expected: false,
      label: 'unauthed never enters static mode'
    },
    { lifecycle: 'ready', url: null, status: 'pending', failed: false, expected: true, label: 'no model row' },
    {
      lifecycle: 'ready',
      url: 'http://x.glb',
      status: 'generating',
      failed: false,
      expected: true,
      label: 'still generating'
    },
    {
      lifecycle: 'ready',
      url: 'http://x.glb',
      status: 'succeeded',
      failed: false,
      expected: false,
      label: 'GLB parsed'
    },
    {
      lifecycle: 'ready',
      url: 'http://x.glb',
      status: 'succeeded',
      failed: true,
      expected: true,
      label: 'GLB load fell to procedural'
    },
    {
      lifecycle: 'onboarding',
      url: null,
      status: 'pending',
      failed: false,
      expected: true,
      label: 'onboarding with wizard closed'
    }
  ]

  for (const { lifecycle, url, status, failed, expected, label } of cases) {
    it(label, () => {
      $companionLifecycle.set(lifecycle)
      setModel(url, status)
      $glbLoadFailed.set(failed)

      expect($staticMode.get()).toBe(expected)
    })
  }
})

describe('semanticRequestFor', () => {
  it('covers all 8 states', () => {
    const states: SpriteStateName[] = [
      'idle',
      'listening',
      'thinking',
      'speaking',
      'working',
      'emotional',
      'interacting',
      'disconnected'
    ]

    for (const state of states) {
      expect(semanticRequestFor(state, null)).toContain('全身立绘')
    }
  })

  it('appends known emotion clauses and falls back for unknown emotions', () => {
    expect(semanticRequestFor('idle', 'happy')).toContain('开心地笑')
    // LLM 自创的情绪仍可通过通用子句解析。
    expect(semanticRequestFor('idle', 'sparkly')).toContain('sparkly')
  })
})
