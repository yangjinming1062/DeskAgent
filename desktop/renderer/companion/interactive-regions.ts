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

// P2-12: replace the module-scoped `regions` array with a per-window
// keyed map. The audit noted that a second window (e.g. a future framed
// tool window with its own SpriteStage) would clobber this single list
// — every window would share the same registry. With a Map the data
// is owned by the window that created it.
const _regionsByWindow = new Map<number, Map<string, InteractiveRegion>>()
const _probesByWindow = new Map<number, () => void>()

function _bucket(windowId: number): Map<string, InteractiveRegion> {
  let m = _regionsByWindow.get(windowId)
  if (!m) {
    m = new Map()
    _regionsByWindow.set(windowId, m)
  }
  return m
}

export function registerInteractiveRegion(id: string, getRect: () => DOMRect | null, windowId: number = 0): void {
  const m = _bucket(windowId)
  m.set(id, { id, getRect })
  _probesByWindow.get(windowId)?.()
}

export function unregisterInteractiveRegion(id: string, windowId: number = 0): void {
  const m = _bucket(windowId)
  if (!m.delete(id)) {return}
  _probesByWindow.get(windowId)?.()
}

export function setCaptureProbe(fn: (() => void) | null, windowId: number = 0): void {
  if (fn === null) {
    _probesByWindow.delete(windowId)
  } else {
    _probesByWindow.set(windowId, fn)
  }
}

export function getInteractiveRegions(windowId: number = 0): ReadonlyArray<InteractiveRegion> {
  return Array.from(_bucket(windowId).values())
}

export function isPointInteractive(x: number, y: number, windowId: number = 0): boolean {
  const regions = _bucket(windowId)
  for (const region of regions.values()) {
    const rect = region.getRect()
    if (!rect) {continue}
    if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) {return true}
  }
  return false
}