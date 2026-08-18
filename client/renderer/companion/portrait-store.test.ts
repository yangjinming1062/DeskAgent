import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  $activeAvatarId,
  $portraitHistory,
  $portraitSelectedIdx,
  clearPortraitHistory,
  hydratePortraitHistory,
  type PortraitEntry,
  pushPortraitEntry,
  selectAvatar,
  setActiveAvatarId
} from './portrait-store'

const avatarEntry = (id: number, overrides: Partial<PortraitEntry> = {}): PortraitEntry => ({
  portraitUrl: `data:portrait-${id}`,
  avatarId: id,
  ...overrides
})

afterEach(() => {
  clearPortraitHistory()
  setActiveAvatarId(null)
  delete (window as { spiritagent?: unknown }).spiritagent
})

describe('pushPortraitEntry', () => {
  it('appends in order and tracks the selected index', () => {
    pushPortraitEntry(avatarEntry(1))
    pushPortraitEntry(avatarEntry(2))

    expect($portraitHistory.get()).toHaveLength(2)
    expect($portraitSelectedIdx.get()).toBe(1)
  })

  it('caps the history at five entries', () => {
    for (let id = 1; id <= 7; id++) {
      pushPortraitEntry(avatarEntry(id))
    }

    const history = $portraitHistory.get()
    expect(history).toHaveLength(5)
    expect(history[0].avatarId).toBe(3)
    expect(history[4].avatarId).toBe(7)
  })
})

describe('hydratePortraitHistory', () => {
  it('hydrates history from backend and aligns selected index to activeAvatarId', async () => {
    setActiveAvatarId(2)

    const mockApi = vi.fn().mockImplementation(({ path }: { path: string }) => {
      if (path === '/api/companion/avatar/history') {
        return Promise.resolve({
          history: [
            { id: 3, asset_url: 'data:p3' },
            { id: 2, asset_url: 'data:p2' },
            { id: 1, asset_url: 'data:p1' }
          ]
        })
      }

      return Promise.resolve(null)
    })

    ;(window as { spiritagent?: unknown }).spiritagent = {
      api: mockApi,
      apiAsset: vi.fn(async ({ url }: { url: string }) => url)
    }

    await hydratePortraitHistory()

    const history = $portraitHistory.get()
    expect(history).toHaveLength(3)
    expect(history[0].avatarId).toBe(1)
    expect(history[1].avatarId).toBe(2)
    expect(history[2].avatarId).toBe(3)
    expect($portraitSelectedIdx.get()).toBe(1)
  })
})

describe('selectAvatar', () => {
  it('calls PUT /api/companion/avatar/{id}/select and updates active avatar id', async () => {
    const mockApi = vi.fn().mockResolvedValue({ id: 5, active: true })

    ;(window as { spiritagent?: unknown }).spiritagent = { api: mockApi }

    const ok = await selectAvatar(5)
    expect(ok).toBe(true)
    expect(mockApi).toHaveBeenCalledWith({
      path: '/api/companion/avatar/5/select',
      method: 'PUT'
    })
    expect($activeAvatarId.get()).toBe(5)
  })
})

describe('clearPortraitHistory', () => {
  it('clears the history and resets the selected index', () => {
    pushPortraitEntry(avatarEntry(1))

    clearPortraitHistory()

    expect($portraitHistory.get()).toEqual([])
    expect($portraitSelectedIdx.get()).toBe(0)
  })
})
