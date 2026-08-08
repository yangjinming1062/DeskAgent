import { atom } from 'nanostores'

// Model + wardrobe asset catalog for the 3D companion.
// Backed by the backend /api/companion/model + /api/companion/wardrobe
// endpoints; pushed over the gateway as model.ready / wardrobe.updated events.

export interface ModelInfo {
  id: number | null
  asset_url: string | null
  species: string | null
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

export const $modelInfo = atom<ModelInfo>({
  id: null,
  asset_url: null,
  species: null,
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
