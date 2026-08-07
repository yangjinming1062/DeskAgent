import { atom } from 'nanostores'
import type { ApiClient } from '../api'

export interface OutfitOverride {
  color?: string
  roughness?: number
  metalness?: number
}

export interface WardrobeItem {
  id: number
  name: string
  category: string
  material_overrides: Record<string, OutfitOverride>
  texture_url: string | null
  equipped: boolean
}

export const $wardrobeItems = atom<WardrobeItem[]>([])
export const $wardrobeOpen = atom(false)

let _applyFn: ((item: WardrobeItem) => void) | null = null

export function setApplyOutfitFn(fn: ((item: WardrobeItem) => void) | null): void {
  _applyFn = fn
}

export function applyOutfit(item: WardrobeItem): void {
  _applyFn?.(item)
}

/** Fetch the user's currently equipped outfit and apply it through the
 * registered engine hook. Swallows network failure silently — no equipped
 * outfit is a normal state. */
export async function refreshEquippedAndApply(api: ApiClient): Promise<void> {
  const item = await api.getEquippedOutfit().catch(() => null)
  if (item) applyOutfit(item)
}
