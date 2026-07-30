import { atom } from 'nanostores'

export interface ClipItem {
  scene: string
  batch: number
  status: 'pending' | 'queued' | 'processing' | 'succeeded' | 'failed'
  url: string | null
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

export function updateClipCatalog(clips: ClipItem[]): void {
  const current = { ...$clipCatalog.get() }
  for (const c of clips) {
    current[c.scene] = c
  }
  $clipCatalog.set(current)
}

export function setClipStatus(scene: string, status: ClipItem['status'], url: string | null): void {
  const current = { ...$clipCatalog.get() }
  const existing = current[scene]
  current[scene] = {
    scene,
    batch: existing?.batch ?? 1,
    status,
    url: url ?? existing?.url ?? null
  }
  $clipCatalog.set(current)
}

export function getClipUrlForScene(scene: string): string | null {
  const catalog = $clipCatalog.get()
  const item = catalog[scene]
  if (item && item.status === 'succeeded' && item.url) {
    return item.url
  }
  return null
}
