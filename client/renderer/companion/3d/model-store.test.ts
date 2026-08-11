import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  $equippedItem,
  $modelInfo,
  $wardrobe,
  $wardrobeCandidates,
  $wardrobePreview,
  $wardrobeSelectedIdx,
  clearWardrobeCandidates,
  hydrateModel,
  hydrateWardrobe,
  pushWardrobeCandidate,
  selectWardrobeCandidate,
  type WardrobeItem
} from './model-store'

const baseModelResponse = {
  id: 7,
  asset_url: 'http://localhost:8000/api/companion/model/file/1/abc.glb?expires=1&sig=1',
  provider: 'base_texture',
  species: '精灵',
  morph_params: { height: 0.4 },
  status: 'succeeded',
  rig_type: 'biped',
  rig_naming: 'mixamo',
  has_rig: true,
  has_morph_targets: true
}

function setWindowDeskagent(api: ReturnType<typeof vi.fn>): void {
  ;(window as { deskagent?: unknown }).deskagent = { api }
}

function restoreWindowDeskagent(): void {
  delete (window as { deskagent?: unknown }).deskagent
}

describe('hydrateModel', () => {
  beforeEach(() => {
    $modelInfo.set({
      id: null,
      asset_url: null,
      species: null,
      provider: null,
      morph_params: {},
      has_rig: false,
      has_morph_targets: false,
      status: 'pending',
      rig_type: 'biped',
      rig_naming: 'mixamo'
    })
    vi.spyOn(console, 'warn').mockImplementation(() => undefined)
  })

  afterEach(() => {
    vi.restoreAllMocks()
    restoreWindowDeskagent()
  })

  it('publishes the active model on a 200 response', async () => {
    const api = vi.fn().mockResolvedValue(baseModelResponse)
    setWindowDeskagent(api)

    await hydrateModel()

    expect(api).toHaveBeenCalledWith({ path: '/api/companion/model' })
    expect($modelInfo.get()).toEqual({
      id: 7,
      asset_url: baseModelResponse.asset_url,
      species: '精灵',
      provider: 'base_texture',
      morph_params: { height: 0.4 },
      has_rig: true,
      has_morph_targets: true,
      status: 'succeeded',
      rig_type: 'biped',
      rig_naming: 'mixamo'
    })
    expect(console.warn).not.toHaveBeenCalled()
  })

  it('treats a 404 as expected (no active row) and leaves the atom alone', async () => {
    $modelInfo.set({ ...$modelInfo.get(), species: '人类' })
    const before = $modelInfo.get()
    const api = vi.fn().mockRejectedValue(new Error('404 /api/companion/model'))
    setWindowDeskagent(api)

    await hydrateModel()

    expect($modelInfo.get()).toEqual(before)
    expect(console.warn).not.toHaveBeenCalled()
  })

  it('warns on a 5xx so a missing model is diagnosable', async () => {
    const api = vi.fn().mockRejectedValue(new Error('500 /api/companion/model: boom'))
    setWindowDeskagent(api)

    await hydrateModel()

    expect($modelInfo.get().status).toBe('pending')
    expect(console.warn).toHaveBeenCalledWith('hydrateModel failed', expect.any(Error))
  })
})

describe('hydrateWardrobe', () => {
  const sample: WardrobeItem[] = [
    {
      id: 1,
      name: '默认',
      category: 'preset',
      material_overrides_json: '{}',
      texture_url: null,
      equipped: false
    },
    {
      id: 2,
      name: '酷炫',
      category: 'generated',
      material_overrides_json: '{}',
      texture_url: 'http://localhost:8000/api/companion/asset/1/x.png?sig=1',
      equipped: true
    }
  ]

  beforeEach(() => {
    $wardrobe.set([])
    $equippedItem.set(null)
    vi.spyOn(console, 'warn').mockImplementation(() => undefined)
  })

  afterEach(() => {
    vi.restoreAllMocks()
    restoreWindowDeskagent()
  })

  it('publishes items and derives the equipped atom', async () => {
    const api = vi.fn().mockResolvedValue(sample)
    setWindowDeskagent(api)

    await hydrateWardrobe()

    expect($wardrobe.get()).toEqual(sample)
    expect($equippedItem.get()?.id).toBe(2)
    expect(console.warn).not.toHaveBeenCalled()
  })

  it('warns on a 5xx', async () => {
    const api = vi.fn().mockRejectedValue(new Error('502 Bad Gateway'))
    setWindowDeskagent(api)

    await hydrateWardrobe()

    expect($wardrobe.get()).toEqual([])
    expect(console.warn).toHaveBeenCalledWith('hydrateWardrobe failed', expect.any(Error))
  })
})

describe('wardrobe candidates & preview', () => {
  beforeEach(() => {
    clearWardrobeCandidates()
  })

  it('pushes candidates and selects the latest', () => {
    pushWardrobeCandidate({
      url: 'http://localhost/c1.png',
      prompt: 'prompt 1',
      fileId: 'fid1',
      description: 'desc 1'
    })

    expect($wardrobeCandidates.get().length).toBe(1)
    expect($wardrobeSelectedIdx.get()).toBe(0)
    expect($wardrobePreview.get()?.texture_url).toBe('http://localhost/c1.png')

    pushWardrobeCandidate({
      url: 'http://localhost/c2.png',
      prompt: 'prompt 2',
      fileId: 'fid2',
      description: 'desc 2'
    })

    expect($wardrobeCandidates.get().length).toBe(2)
    expect($wardrobeSelectedIdx.get()).toBe(1)
    expect($wardrobePreview.get()?.texture_url).toBe('http://localhost/c2.png')
  })

  it('caps candidate history at 3 and shifts oldest', () => {
    for (let i = 1; i <= 4; i++) {
      pushWardrobeCandidate({
        url: `http://localhost/c${i}.png`,
        prompt: `prompt ${i}`,
        fileId: `fid${i}`,
        description: `desc ${i}`
      })
    }

    const current = $wardrobeCandidates.get()
    expect(current.length).toBe(3)
    expect(current.map(c => c.fileId)).toEqual(['fid2', 'fid3', 'fid4'])
    expect($wardrobeSelectedIdx.get()).toBe(2)
    expect($wardrobePreview.get()?.texture_url).toBe('http://localhost/c4.png')
  })

  it('switches preview when selecting an earlier candidate', () => {
    pushWardrobeCandidate({
      url: 'http://localhost/c1.png',
      prompt: 'p1',
      fileId: 'fid1',
      description: 'd1'
    })
    pushWardrobeCandidate({
      url: 'http://localhost/c2.png',
      prompt: 'p2',
      fileId: 'fid2',
      description: 'd2'
    })

    selectWardrobeCandidate(0)
    expect($wardrobeSelectedIdx.get()).toBe(0)
    expect($wardrobePreview.get()?.texture_url).toBe('http://localhost/c1.png')
  })

  it('clears all candidates and resets preview to null', () => {
    pushWardrobeCandidate({
      url: 'http://localhost/c1.png',
      prompt: 'p1',
      fileId: 'fid1',
      description: 'd1'
    })

    clearWardrobeCandidates()
    expect($wardrobeCandidates.get()).toEqual([])
    expect($wardrobeSelectedIdx.get()).toBe(0)
    expect($wardrobePreview.get()).toBeNull()
  })
})
