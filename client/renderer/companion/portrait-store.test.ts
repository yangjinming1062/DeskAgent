import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  $activeAvatarId,
  $portraitHistory,
  $portraitSelectedIdx,
  clearPortraitHistory,
  commitPortraitEntry,
  hydratePortraitHistory,
  type PortraitEntry,
  pushPortraitEntry,
  selectAvatar,
  setActiveAvatarId
} from './portrait-store'

const avatarEntry = (id: number, overrides: Partial<PortraitEntry> = {}): PortraitEntry => ({
  portraitUrl: `data:portrait-${id}`,
  avatarId: id,
  seedUrls: null,
  ...overrides
})

afterEach(() => {
  clearPortraitHistory()
  setActiveAvatarId(null)
  delete (window as { spiritagent?: unknown }).spiritagent
})

describe('commitPortraitEntry', () => {
  it('pushes when the avatar id is new', () => {
    pushPortraitEntry(avatarEntry(1))
    pushPortraitEntry(avatarEntry(2))

    commitPortraitEntry(avatarEntry(3, { seedUrls: { front: 'f3', right: null, back: null } }))

    expect($portraitHistory.get()).toHaveLength(3)
    expect($portraitHistory.get()[2]).toMatchObject({ avatarId: 3, seedUrls: { front: 'f3', right: null, back: null } })
    expect($portraitSelectedIdx.get()).toBe(2)
  })

  it('merges seeds into the existing entry by avatarId', () => {
    pushPortraitEntry(avatarEntry(1))
    pushPortraitEntry(avatarEntry(2, { seedUrls: { front: 'old-front', right: null, back: null } }))
    pushPortraitEntry(avatarEntry(3))

    // Regen A2's front — should NOT add a 4th entry.
    commitPortraitEntry(avatarEntry(2, { seedUrls: { front: 'new-front', right: null, back: null } }))

    const history = $portraitHistory.get()

    expect(history).toHaveLength(3)
    expect(history[1]).toMatchObject({ avatarId: 2, seedUrls: { front: 'new-front', right: null, back: null } })
    // Inner selection should re-anchor on the merged entry, not the latest push.
    expect($portraitSelectedIdx.get()).toBe(1)
  })

  it('preserves portraitUrl when the new entry omits it', () => {
    pushPortraitEntry(avatarEntry(7, { portraitUrl: 'data:keep-me' }))

    commitPortraitEntry({ portraitUrl: null, avatarId: 7, seedUrls: { front: 'f', right: null, back: null } })

    expect($portraitHistory.get()[0].portraitUrl).toBe('data:keep-me')
  })

  it('falls through to push when either side has null avatarId', () => {
    pushPortraitEntry(avatarEntry(1))

    commitPortraitEntry({ portraitUrl: null, avatarId: null, seedUrls: null })

    expect($portraitHistory.get()).toHaveLength(2)
  })
})

describe('hydratePortraitHistory', () => {
  it('hydrates history from backend and aligns selected index to activeAvatarId', async () => {
    setActiveAvatarId(2)

    const mockApi = vi.fn().mockImplementation(({ path }: { path: string }) => {
      if (path === '/api/companion/avatar/history') {
        return Promise.resolve({
          history: [
            { id: 3, asset_url: 'data:p3', seed_front_url: null, seed_right_url: null, seed_back_url: null },
            { id: 2, asset_url: 'data:p2', seed_front_url: 'data:f2', seed_right_url: null, seed_back_url: null },
            { id: 1, asset_url: 'data:p1', seed_front_url: null, seed_right_url: null, seed_back_url: null }
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
