import { atom } from 'nanostores'

import { isClientErrorIpc } from '@/shared/lib/ipc-error'

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
}

export interface WardrobeItem {
  id: number
  name: string
  category: string
  // Raw JSON blob from the backend — parse before applying material overrides.
  material_overrides_json: string
  texture_url: string | null
  // PBR channels paired with `texture_url` (albedo). All nullable: legacy
  // rows + colour-preset rows only carry `texture_url`.
  normal_url?: string | null
  roughness_url?: string | null
  metalness_url?: string | null
  prompt?: string | null
  equipped: boolean
}

interface CompanionModelResponse {
  id: number
  asset_url: string | null
  provider: string
  species: string
  morph_params: Record<string, number>
  status: string
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
  status: 'pending'
})

export const $wardrobe = atom<WardrobeItem[]>([])
export const $equippedItem = atom<WardrobeItem | null>(null)

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
      status: res.status
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
