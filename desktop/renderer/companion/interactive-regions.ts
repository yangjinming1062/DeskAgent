// `setIgnoreMouseEvents` on Electron is window-level binary: it either captures
// the whole window or lets every click pass through to the apps behind. The
// sprite window is screen-sized + transparent, so we want to capture ONLY the
// visible rects (sprite + any open overlay) and stay click-through everywhere
// else. Each overlay registers its bbox here; the SpriteStage's global
// mousemove listener hit-tests against the union and is the sole caller of
// `setIgnoreMouseEvents`.
export type InteractiveRegion = {
  getRect: () => DOMRect | null
  id: string
}

let regions: InteractiveRegion[] = []

// Re-evaluates the capture state against the last-known cursor position.
// SpriteStage installs this; overlays call it on mount so a dialog opened under
// the cursor captures immediately instead of waiting for the next mousemove.
let captureProbe: (() => void) | null = null

export function registerInteractiveRegion(id: string, getRect: () => DOMRect | null): void {
  regions = regions.filter(r => r.id !== id)

  regions.push({ id, getRect })

  captureProbe?.()
}

export function unregisterInteractiveRegion(id: string): void {
  const next = regions.filter(r => r.id !== id)

  if (next.length === regions.length) {return}

  regions = next
  captureProbe?.()
}

export function setCaptureProbe(fn: (() => void) | null): void {
  captureProbe = fn
}

export function getInteractiveRegions(): ReadonlyArray<InteractiveRegion> {
  return regions
}

export function isPointInteractive(x: number, y: number): boolean {
  for (const region of regions) {
    const rect = region.getRect()

    if (!rect) {continue}

    if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) {return true}
  }

  return false
}