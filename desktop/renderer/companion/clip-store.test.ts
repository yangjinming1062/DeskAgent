import { describe, expect, it } from 'vitest'

import { $clipCatalog, getClipUrlForScene, setClipStatus, updateClipCatalog } from './clip-store'

describe('clip-store catalog and clip status management', () => {
  it('updates clip catalog and retrieves succeeded clip URLs', () => {
    updateClipCatalog([
      { scene: 'idle', batch: 0, status: 'succeeded', url: 'http://media/idle.webm' },
      { scene: 'speaking', batch: 1, status: 'processing', url: null }
    ])

    expect(getClipUrlForScene('idle')).toBe('http://media/idle.webm')
    expect(getClipUrlForScene('speaking')).toBeNull()
  })

  it('sets individual clip status dynamically', () => {
    setClipStatus('speaking', 'succeeded', 'http://media/speaking.webm')
    expect(getClipUrlForScene('speaking')).toBe('http://media/speaking.webm')
  })
})
