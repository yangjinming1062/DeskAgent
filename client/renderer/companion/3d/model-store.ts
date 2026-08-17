import { atom, computed } from 'nanostores'

import { isClientErrorIpc } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { safeJsonParse } from '@/shared/lib/safe-json'

import type { ClipDef } from './clips-biped'
import type { RenderStyle } from './style/types'

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
  content_hash: string | null
  // Seed style the model was generated from — routes NPR vs PBR rendering.
  style: RenderStyle
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
  displacement_url?: string | null
  prompt?: string | null
  outfit_description?: string | null
  equipped: boolean
  origin?: string
  gift_state?: string | null
  gift_reason?: string | null
  gift_message?: string | null
  // Geometric wardrobe (PROTOCOL.md §1.6). ``slot`` is the backend-resolved
  // mutual-exclusion slot; absent on client-built preview candidates.
  kind?: string
  mesh_url?: string | null
  assembly_json?: string
  slot?: string
}

export interface CompanionExpression {
  id: number
  name: string
  label: string
  valence: string
  description: string
  weights: Record<string, number>
  tags: string[]
  scale_boost: number
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
  style?: string
  has_rig: boolean
  has_morph_targets: boolean
  content_hash?: string | null
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
  rig_naming: 'mixamo',
  content_hash: null,
  style: 'realistic'
})

// Session-level render-style override. Reset to the model's own style
// whenever a different model becomes active (Companion3D watches id).
export const $renderStyle = atom<RenderStyle>('realistic')

export const $wardrobe = atom<WardrobeItem[]>([])
// True once the first loadCharacter() settles (GLB parsed or procedural
// fallback active). Gates the render-power scheduler: before it, hatching
// runs at full frame rate regardless of idle/sleep signals.
export const $modelLoadSettled = atom<boolean>(false)
// Multi-equip: up to one item per slot (outfit / torso / legs / feet / head / …)
// can be equipped at once; the array holds the full equipped set.
export const $equippedItems = atom<WardrobeItem[]>([])
export const $availableClipNames = atom<Set<string>>(new Set())
export const $generatedClips = atom<ClipDef[]>([])

/** Resolve mutual-exclusion slot from item or assembly_json. */
export function slotOf(item: WardrobeItem): string {
  if (item.slot) {
    return item.slot
  }

  if ((item.kind ?? 'texture') === 'texture' || !item.mesh_url) {
    return 'outfit'
  }

  const asm = safeJsonParse<unknown>(item.assembly_json ?? '{}', {})

  const slot = asm && typeof asm === 'object' && !Array.isArray(asm) ? (asm as { slot?: unknown }).slot : null

  return typeof slot === 'string' && slot ? slot : 'torso'
}

// ── 换装候选回溯（镜像 $portraitHistory）──
export interface WardrobeCandidate {
  url: string
  prompt: string
  fileId: string
  description: string
  normalUrl?: string
  normalFileId?: string
  roughnessUrl?: string
  roughnessFileId?: string
  metalnessUrl?: string
  metalnessFileId?: string
  displacementUrl?: string
  displacementFileId?: string
  // Geometric wardrobe (PROTOCOL.md §1.6).
  meshUrl?: string
  meshFileId?: string
  kind?: string
  assemblyJson?: string
}

const _MAX_CANDIDATES = 3

export const $wardrobeCandidates = atom<WardrobeCandidate[]>([])
export const $wardrobeSelectedIdx = atom<number>(0)
// 选中候选时设为临时 outfit spec；null 时回退到 $equippedItems
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
    normal_url: c.normalUrl ?? null,
    roughness_url: c.roughnessUrl ?? null,
    metalness_url: c.metalnessUrl ?? null,
    displacement_url: c.displacementUrl ?? null,
    prompt: c.prompt,
    equipped: false,
    kind: c.kind ?? 'texture',
    mesh_url: c.meshUrl ?? null,
    assembly_json: c.assemblyJson ?? '{}'
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

/** Render set: equipped items overlaid with active preview candidate by slot. */
export const $outfitView = computed([$equippedItems, $wardrobePreview], (equipped, preview) =>
  preview ? equipped.filter(i => slotOf(i) !== slotOf(preview)).concat(preview) : equipped
)

export function setWardrobe(items: WardrobeItem[]): void {
  $wardrobe.set(items)
  $equippedItems.set(items.filter(i => i.equipped))
}

export function refreshEquippedAndApply(): WardrobeItem[] {
  const equipped = $wardrobe.get().filter(i => i.equipped)
  $equippedItems.set(equipped)

  return equipped
}

// Pull the active model from the backend on lifecycle=ready. The backend
// re-signs ``asset_url`` every call (5-minute TTL); we never cache it. 404
// (no model yet during onboarding) is swallowed silently so the initial
// atom stays at its default; 5xx / network errors warn so a missing model
// doesn't go unnoticed in production.
export async function ensureModelGeneration(): Promise<void> {
  try {
    await window.spiritagent.api<{ id?: number; status?: string }>({
      path: '/api/companion/model',
      method: 'POST',
      body: {}
    })
  } catch (err) {
    log.info('model-store', 'ensureModelGeneration requested:', err)
  }
}

export async function hydrateModel(): Promise<void> {
  try {
    const res = await window.spiritagent.api<CompanionModelResponse>({
      path: '/api/companion/model'
    })

    if (res && res.asset_url && res.status === 'succeeded') {
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
        rig_naming: res.rig_naming ?? 'mixamo',
        content_hash: res.content_hash ?? null,
        style: res.style === 'anime' ? 'anime' : 'realistic'
      })

      return
    }
  } catch (error) {
    if (!isClientErrorIpc(error)) {
      log.warn('model-store', 'hydrateModel failed', error)
    }
  }

  // If no ready model is available on lifecycle=ready, automatically trigger generation
  void ensureModelGeneration()
}

// Same shape as ``hydrateModel`` — GET /api/companion/wardrobe, publish to
// ``$wardrobe`` (which also derives ``$equippedItems``). Shared between the
// lifecycle=ready hydration and the ``wardrobe.updated`` WS event handler.
export async function hydrateWardrobe(): Promise<void> {
  try {
    const res = await window.spiritagent.api<WardrobeItem[]>({ path: '/api/companion/wardrobe' })
    setWardrobe(res ?? [])
  } catch (error) {
    if (!isClientErrorIpc(error)) {
      log.warn('model-store', 'hydrateWardrobe failed', error)
    }
  }
}

export const $expressions = atom<CompanionExpression[]>([])

/** GET → atom hydrator. Swallows 4xx (precondition) silently so the atom stays
 * at its default; 5xx / network errors warn so a missing asset doesn't go
 * unnoticed in production. */
async function hydrateArray<T>(
  path: string,
  atom: { set(value: T[]): void },
  arrayKey: string,
  label: string
): Promise<void> {
  try {
    const res = await window.spiritagent.api<Record<string, unknown>>({ path })
    const arr = res?.[arrayKey]

    if (Array.isArray(arr)) {
      atom.set(arr as T[])
    }
  } catch (error) {
    if (!isClientErrorIpc(error)) {
      log.warn('model-store', `${label} failed`, error)
    }
  }
}

export async function hydrateGeneratedClips(): Promise<void> {
  await hydrateArray<ClipDef>('/api/companion/animations', $generatedClips, 'clips', 'hydrateGeneratedClips')
}

export async function hydrateExpressions(): Promise<void> {
  await hydrateArray<CompanionExpression>(
    '/api/companion/expressions',
    $expressions,
    'expressions',
    'hydrateExpressions'
  )
}
