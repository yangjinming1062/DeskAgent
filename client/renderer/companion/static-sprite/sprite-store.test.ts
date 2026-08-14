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

interface DeskagentMock {
  api: ReturnType<typeof vi.fn>
  apiAsset: ReturnType<typeof vi.fn>
}

function setWindowDeskagent(mock: DeskagentMock): void {
  ;(window as { deskagent?: unknown }).deskagent = { api: mock.api, apiAsset: mock.apiAsset }
}

function restoreWindowDeskagent(): void {
  delete (window as { deskagent?: unknown }).deskagent
}

function setModel(assetUrl: string | null, status: string): void {
  $modelInfo.set({ ...$modelInfo.get(), asset_url: assetUrl, status })
}

describe('sprite-store', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    // Real-scale epoch: the store's initial _lastPostAt=0 sentinel must not
    // be confused with "just posted" (a 0-epoch clock would block request #1).
    vi.setSystemTime(new Date('2026-08-14T12:00:00Z').getTime())
    resetSpriteAlbum()
    $glbLoadFailed.set(false)
    setModel(null, 'pending')
    $companionLifecycle.set('ready')
  })

  afterEach(() => {
    vi.useRealTimers()
    restoreWindowDeskagent()
  })

  it('publishes the resolved sprite and caches by content_hash', async () => {
    const mock: DeskagentMock = {
      api: vi.fn().mockResolvedValue(spriteResponse('h1', '等待')),
      apiAsset: vi.fn().mockResolvedValue('data:image/png;base64,AAA')
    }

    setWindowDeskagent(mock)

    await requestSprite(WAITING_REQUEST, 'waiting')
    expect($activeSprite.get()).toEqual({ dataUrl: 'data:image/png;base64,AAA', tag: '等待' })

    // Second request resolving the same hash (album match) must not re-fetch the asset.
    mock.api.mockResolvedValue(spriteResponse('h1', '等待'))
    await requestSprite('另一种语义请求')
    expect(mock.apiAsset).toHaveBeenCalledTimes(1)
  })

  it('dedupes concurrent identical requests into one POST', async () => {
    const mock: DeskagentMock = {
      api: vi.fn().mockResolvedValue(spriteResponse('h2', '等待')),
      apiAsset: vi.fn().mockResolvedValue('data:AAA')
    }

    setWindowDeskagent(mock)

    await Promise.all([requestSprite('同一个请求'), requestSprite('同一个请求')])
    expect(mock.api).toHaveBeenCalledTimes(1)
  })

  it('keeps the current image when the backend fails', async () => {
    const mock: DeskagentMock = {
      api: vi.fn().mockResolvedValueOnce(spriteResponse('h3', '等待')).mockRejectedValueOnce(new Error('down')),
      apiAsset: vi.fn().mockResolvedValue('data:AAA')
    }

    setWindowDeskagent(mock)

    await requestSprite(WAITING_REQUEST, 'waiting')
    const before = $activeSprite.get()

    await requestSprite('会失败的请求')
    expect($activeSprite.get()).toBe(before)
  })

  it('spaces distinct requests by 1.5s', async () => {
    const mock: DeskagentMock = {
      api: vi.fn().mockResolvedValue(spriteResponse('h4', '等待')),
      apiAsset: vi.fn().mockResolvedValue('data:AAA')
    }

    setWindowDeskagent(mock)

    await requestSprite('第一个请求')
    await requestSprite('第二个请求')
    expect(mock.api).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(1500)
    await requestSprite('第三个请求')
    expect(mock.api).toHaveBeenCalledTimes(2)
  })

  it('resetSpriteAlbum drops caches so identical requests re-fetch', async () => {
    const mock: DeskagentMock = {
      api: vi.fn().mockResolvedValue(spriteResponse('h5', '等待')),
      apiAsset: vi.fn().mockResolvedValue('data:AAA')
    }

    setWindowDeskagent(mock)

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
  it('covers all 9 states', () => {
    const states: SpriteStateName[] = [
      'idle',
      'listening',
      'thinking',
      'speaking',
      'working',
      'emotional',
      'sleeping',
      'interacting',
      'disconnected'
    ]

    for (const state of states) {
      expect(semanticRequestFor(state, null)).toContain('全身立绘')
    }
  })

  it('appends known emotion clauses and falls back for unknown emotions', () => {
    expect(semanticRequestFor('idle', 'happy')).toContain('开心地笑')
    // LLM-invented emotions still resolve through the generic clause.
    expect(semanticRequestFor('idle', 'sparkly')).toContain('sparkly')
  })
})
