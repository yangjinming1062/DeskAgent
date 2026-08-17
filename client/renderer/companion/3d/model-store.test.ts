import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { log } from '@/shared/lib/log'

import {
  $equippedItems,
  $modelInfo,
  $outfitView,
  $wardrobe,
  $wardrobeCandidates,
  $wardrobePreview,
  $wardrobeSelectedIdx,
  clearWardrobeCandidates,
  hydrateModel,
  hydrateWardrobe,
  pushWardrobeCandidate,
  selectWardrobeCandidate,
  slotOf,
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
  style: 'anime',
  has_rig: true,
  has_morph_targets: true,
  content_hash: 'sha256_mock_hash_123'
}

function setWindowSpiritagent(api: ReturnType<typeof vi.fn>): void {
  ;(window as { spiritagent?: unknown }).spiritagent = { api }
}

function restoreWindowSpiritagent(): void {
  delete (window as { spiritagent?: unknown }).spiritagent
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
      rig_naming: 'mixamo',
      content_hash: null,
      style: 'realistic'
    })
    vi.spyOn(log, 'warn').mockImplementation(() => undefined)
  })

  afterEach(() => {
    vi.restoreAllMocks()
    restoreWindowSpiritagent()
  })

  it('publishes the active model on a 200 response', async () => {
    const api = vi.fn().mockResolvedValue(baseModelResponse)
    setWindowSpiritagent(api)

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
      rig_naming: 'mixamo',
      content_hash: 'sha256_mock_hash_123',
      style: 'anime'
    })
    expect(log.warn).not.toHaveBeenCalled()
  })

  it('treats a 404 as expected (no active row) and triggers model generation', async () => {
    $modelInfo.set({ ...$modelInfo.get(), species: '人类' })
    const before = $modelInfo.get()

    const api = vi.fn().mockImplementation(async req => {
      if (req.path === '/api/companion/model' && req.method !== 'POST') {
        throw new Error('404 /api/companion/model')
      }

      return { id: 1, status: 'generating' }
    })

    setWindowSpiritagent(api)

    await hydrateModel()

    expect($modelInfo.get()).toEqual(before)
    expect(api).toHaveBeenCalledWith({ path: '/api/companion/model' })
    expect(api).toHaveBeenCalledWith({ path: '/api/companion/model', method: 'POST', body: {} })
    expect(log.warn).not.toHaveBeenCalled()
  })

  it('warns on a 5xx so a missing model is diagnosable', async () => {
    const api = vi.fn().mockImplementation(async req => {
      if (req.path === '/api/companion/model' && req.method !== 'POST') {
        throw new Error('500 /api/companion/model: boom')
      }

      return { id: 1, status: 'generating' }
    })

    setWindowSpiritagent(api)

    await hydrateModel()

    expect($modelInfo.get().status).toBe('pending')
    expect(log.warn).toHaveBeenCalledWith('model-store', 'hydrateModel failed', expect.any(Error))
  })
})

describe('hydrateWardrobe', () => {
  const sample: WardrobeItem[] = [
    {
      id: 1,
      name: '休闲装',
      category: 'generated',
      material_overrides_json: '{}',
      texture_url: 'http://localhost:8000/api/companion/asset/1/a.png?sig=1',
      outfit_description: '简约的白色T恤搭配牛仔裤',
      equipped: false
    },
    {
      id: 2,
      name: '酷炫',
      category: 'generated',
      material_overrides_json: '{}',
      texture_url: 'http://localhost:8000/api/companion/asset/1/x.png?sig=1',
      outfit_description: '黑色皮夹克配银色饰品',
      equipped: true
    }
  ]

  beforeEach(() => {
    $wardrobe.set([])
    $equippedItems.set([])
    vi.spyOn(log, 'warn').mockImplementation(() => undefined)
  })

  afterEach(() => {
    vi.restoreAllMocks()
    restoreWindowSpiritagent()
  })

  it('publishes items and derives the equipped atom', async () => {
    const api = vi.fn().mockResolvedValue(sample)
    setWindowSpiritagent(api)

    await hydrateWardrobe()

    expect($wardrobe.get()).toEqual(sample)
    expect($equippedItems.get().map(i => i.id)).toEqual([2])
    expect(log.warn).not.toHaveBeenCalled()
  })

  it('warns on a 5xx', async () => {
    const api = vi.fn().mockRejectedValue(new Error('502 Bad Gateway'))
    setWindowSpiritagent(api)

    await hydrateWardrobe()

    expect($wardrobe.get()).toEqual([])
    expect(log.warn).toHaveBeenCalledWith('model-store', 'hydrateWardrobe failed', expect.any(Error))
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

  it('switches preview when selecting an earlier candidate and preserves displacement channel', () => {
    pushWardrobeCandidate({
      url: 'http://localhost/c1.png',
      normalUrl: 'http://localhost/n1.png',
      roughnessUrl: 'http://localhost/r1.png',
      metalnessUrl: 'http://localhost/m1.png',
      displacementUrl: 'http://localhost/d1.png',
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
    const prev = $wardrobePreview.get()
    expect(prev?.texture_url).toBe('http://localhost/c1.png')
    expect(prev?.normal_url).toBe('http://localhost/n1.png')
    expect(prev?.roughness_url).toBe('http://localhost/r1.png')
    expect(prev?.metalness_url).toBe('http://localhost/m1.png')
    expect(prev?.displacement_url).toBe('http://localhost/d1.png')
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

describe('slotOf and $outfitView', () => {
  it('identifies item slot correctly', () => {
    const textureItem: WardrobeItem = {
      id: 1,
      name: '材质',
      category: 'generated',
      material_overrides_json: '{}',
      texture_url: 'http://localhost/t.png',
      equipped: true,
      kind: 'texture'
    }

    expect(slotOf(textureItem)).toBe('outfit')

    const garmentItem: WardrobeItem = {
      id: 2,
      name: '上衣',
      category: 'generated',
      material_overrides_json: '{}',
      texture_url: null,
      mesh_url: 'http://localhost/m.glb',
      equipped: true,
      kind: 'garment',
      assembly_json: JSON.stringify({ kind: 'garment', slot: 'torso' })
    }

    expect(slotOf(garmentItem)).toBe('torso')

    const explicitSlotItem: WardrobeItem = {
      id: 3,
      name: '头饰',
      category: 'generated',
      material_overrides_json: '{}',
      texture_url: null,
      equipped: true,
      slot: 'head'
    }

    expect(slotOf(explicitSlotItem)).toBe('head')
  })

  it('replaces only the matching slot during candidate preview in $outfitView', () => {
    const torsoItem: WardrobeItem = {
      id: 1,
      name: '外套',
      category: 'generated',
      material_overrides_json: '{}',
      texture_url: null,
      mesh_url: 'http://localhost/torso.glb',
      equipped: true,
      kind: 'garment',
      assembly_json: JSON.stringify({ kind: 'garment', slot: 'torso' })
    }

    const legsItem: WardrobeItem = {
      id: 2,
      name: '裤子',
      category: 'generated',
      material_overrides_json: '{}',
      texture_url: null,
      mesh_url: 'http://localhost/legs.glb',
      equipped: true,
      kind: 'garment',
      assembly_json: JSON.stringify({ kind: 'garment', slot: 'legs' })
    }

    $equippedItems.set([torsoItem, legsItem])
    clearWardrobeCandidates()

    // Without preview, $outfitView equals $equippedItems
    expect($outfitView.get().map(i => i.name)).toEqual(['外套', '裤子'])

    // Push a candidate for 'torso'
    pushWardrobeCandidate({
      url: 'http://localhost/preview_torso.png',
      fileId: 'fid_torso',
      description: 'preview torso desc',
      meshUrl: 'http://localhost/preview_torso.glb',
      prompt: 'preview torso',
      assemblyJson: JSON.stringify({ kind: 'garment', slot: 'torso' }),
      kind: 'garment'
    })

    // $outfitView should keep legs and replace torso with the preview item
    const currentView = $outfitView.get()
    expect(currentView.length).toBe(2)
    expect(currentView.map(i => slotOf(i))).toEqual(['legs', 'torso'])
    expect(currentView.find(i => slotOf(i) === 'torso')?.id).toBe(-1)
  })
})
