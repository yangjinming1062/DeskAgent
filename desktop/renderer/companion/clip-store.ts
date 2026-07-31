import { atom } from 'nanostores'

export interface ClipMeta {
  frames: number
  cols: number
  fps: number
}

export interface ClipItem {
  scene: string
  batch: number
  /** Active tier; coerced on insert when omitted (succeeded+url -> 3, else 1). */
  tier?: number
  status: 'pending' | 'queued' | 'processing' | 'succeeded' | 'failed'
  url: string | null
  keyframe_url?: string | null
  keyframe_meta?: ClipMeta | null
}

export type ClipCatalog = Record<string, ClipItem>

export const $clipCatalog = atom<ClipCatalog>({})
export const $activeTransitionClip = atom<string | null>(null)

let transitionTimer: ReturnType<typeof setTimeout> | null = null

export function playTransitionClip(scene: string, durationMs = 3000): void {
  if (transitionTimer) {
    clearTimeout(transitionTimer)
  }

  $activeTransitionClip.set(scene)
  transitionTimer = setTimeout(() => {
    transitionTimer = null
    $activeTransitionClip.set(null)
  }, durationMs)
}

 
type ClipPatch = Partial<Omit<ClipItem, 'status'>> & { status?: string }

export function computeTier(item: ClipPatch): number {
  return item.tier ?? (item.status === 'succeeded' && item.url ? 3 : 1)
}

function mergeClip(scene: string, partial: ClipPatch): ClipItem {
  const existing = $clipCatalog.get()[scene]

  const merged: ClipItem = {
    scene,
    batch: existing?.batch ?? 1,
    tier: computeTier(partial),
    status: (partial.status as ClipItem['status'] | undefined) ?? existing?.status ?? 'succeeded',
    url: partial.url ?? existing?.url ?? null,
    keyframe_url: partial.keyframe_url ?? existing?.keyframe_url ?? null,
    keyframe_meta: partial.keyframe_meta ?? existing?.keyframe_meta ?? null,
  }

  return merged
}

export function updateClipCatalog(clips: ClipItem[]): void {
  const current = { ...$clipCatalog.get() }

  for (const c of clips) {
    current[c.scene] = mergeClip(c.scene, c)
  }

  $clipCatalog.set(current)
}

export function setClipStatus(scene: string, status: ClipItem['status'], url: string | null): void {
  const merged = mergeClip(scene, { status, url })

  // Preserve the existing tier unless the new status is a definitive success.
  if (!(status === 'succeeded' && url)) {
    const existing = $clipCatalog.get()[scene]

    if (existing?.tier) {merged.tier = existing.tier}
  }

  $clipCatalog.set({ ...$clipCatalog.get(), [scene]: merged })
}

export function applyClipUpdate(update: {
  scene: string
  tier: number
  status?: string
  url?: string | null
  keyframe_url?: string | null
  keyframe_meta?: ClipMeta | null
}): void {
  const merged = mergeClip(update.scene, update)
  merged.tier = update.tier
  $clipCatalog.set({ ...$clipCatalog.get(), [update.scene]: merged })
}

export function clearClipCatalog(): void {
  $clipCatalog.set({})
  $activeTransitionClip.set(null)
}

export interface ClipAsset {
  tier: number
  url: string | null
  keyframe_meta: ClipMeta | null
}

export function getClipAsset(scene: string): ClipAsset {
  const catalog = $clipCatalog.get()

  const pick = (s: string | undefined): ClipAsset | null => {
    if (!s) {return null}
    const item = catalog[s]

    if (!item) {return null}
    const tier = computeTier(item)

    if (tier >= 2 && item.url) {
      return { tier, url: item.url, keyframe_meta: item.keyframe_meta ?? null }
    }

    return null
  }

  return pick(scene) ?? pick('idle') ?? { tier: 1, url: null, keyframe_meta: null }
}
