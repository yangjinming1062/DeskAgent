import { afterEach, describe, expect, it } from 'vitest'

import {
  $portraitHistory,
  $portraitSelectedIdx,
  clearPortraitHistory,
  commitPortraitEntry,
  type PortraitEntry,
  pushPortraitEntry
} from './portrait-store'

const avatarEntry = (id: number, overrides: Partial<PortraitEntry> = {}): PortraitEntry => ({
  portraitUrl: `data:portrait-${id}`,
  avatarId: id,
  seedUrls: null,
  ...overrides
})

afterEach(() => {
  clearPortraitHistory()
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
