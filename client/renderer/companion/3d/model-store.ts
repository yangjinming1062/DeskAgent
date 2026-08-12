import { atom } from 'nanostores'

import { isClientErrorIpc } from '@/shared/lib/ipc-error'

import type { ClipDef } from './clips-biped'

// Model + wardrobe asset catalog for the 3D companion.
// Backed by the backend /api/companion/model + /api/companion/wardrobe
// endpoints; pushed over the gateway as model.ready / wardrobe.updated events,
// and pulled on lifecycle=ready via ``hydrateModel`` / ``hydrateWardrobe``.

export interface ModelInfo {
  id: number | null
  asset_url: string | null
  species: string | null
  provider: string | null
  morph_params: Record<string, number>
  has_rig: boolean
  has_morph_targets: boolean
  status: string
  rig_type: string
  rig_naming: string
}

export interface WardrobeItem {
  id: number
  name: string
  category: string
  // Raw JSON blob from the backend — parse before applying material overrides.
  material_overrides_json: string
  texture_url: string | null
  // PBR channels paired with `texture_url` (albedo). All nullable.
  normal_url?: string | null
  roughness_url?: string | null
  metalness_url?: string | null
  prompt?: string | null
  outfit_description?: string | null
  equipped: boolean
}

interface CompanionModelResponse {
  id: number
  asset_url: string | null
  provider: string
  species: string
  morph_params: Record<string, number>
  status: string
  rig_type: string
  rig_naming: string
  has_rig: boolean
  has_morph_targets: boolean
}

export const $modelInfo = atom<ModelInfo>({
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

export const $wardrobe = atom<WardrobeItem[]>([])
export const $equippedItem = atom<WardrobeItem | null>(null)
export const $availableClipNames = atom<Set<string>>(new Set())
export const $generatedClips = atom<ClipDef[]>([])

// ── 换装候选回溯（镜像 $portraitHistory）──
export interface WardrobeCandidate {
  url: string
  prompt: string
  fileId: string
  description: string
}

const _MAX_CANDIDATES = 3

export const $wardrobeCandidates = atom<WardrobeCandidate[]>([])
export const $wardrobeSelectedIdx = atom<number>(0)
// 选中候选时设为临时 outfit spec；null 时回退到 $equippedItem
export const $wardrobePreview = atom<WardrobeItem | null>(null)

// Build the transient WardrobeItem consumed by CharacterController.setOutfit
// during preview. Centralised so the id:-1 sentinel lives in one place.
function _candidateToPreview(c: WardrobeCandidate): WardrobeItem {
  return {
    id: -1,
    name: 'preview',
    category: 'draft',
    material_overrides_json: '{}',
    texture_url: c.url,
    prompt: c.prompt,
    equipped: false
  }
}

export function pushWardrobeCandidate(c: WardrobeCandidate): void {
  const current = $wardrobeCandidates.get()
  const next = [...current, c]

  if (next.length > _MAX_CANDIDATES) {
    next.shift()
  }

  $wardrobeCandidates.set(next)
  $wardrobeSelectedIdx.set(next.length - 1)
  $wardrobePreview.set(_candidateToPreview(c))
}

export function selectWardrobeCandidate(idx: number): void {
  const current = $wardrobeCandidates.get()

  if (idx >= 0 && idx < current.length) {
    $wardrobeSelectedIdx.set(idx)
    $wardrobePreview.set(_candidateToPreview(current[idx]))
  }
}

export function clearWardrobeCandidates(): void {
  $wardrobeCandidates.set([])
  $wardrobeSelectedIdx.set(0)
  $wardrobePreview.set(null)
}

// ── Generation progress tracking ──
// model.gen.progress → $modelGenState='generating' + $modelGenProgress
// model.ready        → $modelGenState='succeeded'
// model.failed       → $modelGenState='failed' + $modelGenError
export type ModelGenState = 'idle' | 'generating' | 'succeeded' | 'failed'
export const $modelGenState = atom<ModelGenState>('idle')
export const $modelGenProgress = atom<{ stage: string; progress: number } | null>(null)
export const $modelGenError = atom<string | null>(null)

export function setModelInfo(next: Partial<ModelInfo>): void {
  $modelInfo.set({ ...$modelInfo.get(), ...next })
}

export function setWardrobe(items: WardrobeItem[]): void {
  $wardrobe.set(items)
  $equippedItem.set(items.find(i => i.equipped) ?? null)
}

export function refreshEquippedAndApply(): WardrobeItem | null {
  const equipped = $wardrobe.get().find(i => i.equipped) ?? null
  $equippedItem.set(equipped)

  return equipped
}

// Pull the active model from the backend on lifecycle=ready. The backend
// re-signs ``asset_url`` every call (5-minute TTL); we never cache it. 404
// (no model yet during onboarding) is swallowed silently so the initial
// atom stays at its default; 5xx / network errors warn so a missing model
// doesn't go unnoticed in production.
export async function hydrateModel(): Promise<void> {
  try {
    const res = await window.deskagent.api<CompanionModelResponse>({
      path: '/api/companion/model'
    })

    if (!res) {
      return
    }

    setModelInfo({
      id: res.id,
      asset_url: res.asset_url,
      species: res.species,
      provider: res.provider,
      morph_params: res.morph_params ?? {},
      has_rig: res.has_rig,
      has_morph_targets: res.has_morph_targets,
      status: res.status,
      rig_type: res.rig_type ?? 'biped',
      rig_naming: res.rig_naming ?? 'mixamo'
    })
  } catch (error) {
    if (!isClientErrorIpc(error)) {
      console.warn('hydrateModel failed', error)
    }
  }
}

// Same shape as ``hydrateModel`` — GET /api/companion/wardrobe, publish to
// ``$wardrobe`` (which also derives ``$equippedItem``). Shared between the
// lifecycle=ready hydration and the ``wardrobe.updated`` WS event handler.
export async function hydrateWardrobe(): Promise<void> {
  try {
    const res = await window.deskagent.api<WardrobeItem[]>({ path: '/api/companion/wardrobe' })
    setWardrobe(res ?? [])
  } catch (error) {
    if (!isClientErrorIpc(error)) {
      console.warn('hydrateWardrobe failed', error)
    }
  }
}

export async function hydrateGeneratedClips(): Promise<void> {
  try {
    const res = await window.deskagent.api<{ clips: ClipDef[] }>({ path: '/api/companion/animations' })

    if (res?.clips && Array.isArray(res.clips)) {
      $generatedClips.set(res.clips)
    }
  } catch (error) {
    if (!isClientErrorIpc(error)) {
      console.warn('hydrateGeneratedClips failed', error)
    }
  }
}
