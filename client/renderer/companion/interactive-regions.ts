import { type RefObject, useEffect } from 'react'

export type InteractiveRegion = {
  getRect: () => DOMRect | null
  hitTest?: (x: number, y: number) => boolean
  id: string
}

// Per-window keyed map so each window owns its regions — a second
// window (its own SpriteStage) can't clobber the shared list.
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

export function registerInteractiveRegion(
  id: string,
  getRect: () => DOMRect | null,
  windowId: number = 0,
  hitTest?: (x: number, y: number) => boolean
): void {
  const m = _bucket(windowId)
  m.set(id, { getRect, hitTest, id })
  _probesByWindow.get(windowId)?.()
}

export function unregisterInteractiveRegion(id: string, windowId: number = 0): void {
  const m = _bucket(windowId)

  if (!m.delete(id)) {
    return
  }

  _probesByWindow.get(windowId)?.()
}

export function setCaptureProbe(fn: (() => void) | null, windowId: number = 0): void {
  if (fn === null) {
    _probesByWindow.delete(windowId)
  } else {
    _probesByWindow.set(windowId, fn)
  }
}

export function isPointInteractive(x: number, y: number, windowId: number = 0): boolean {
  const regions = _bucket(windowId)

  for (const region of regions.values()) {
    const rect = region.getRect()

    if (!rect) {
      continue
    }

    if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) {
      // Rect hit refined by the pixel predicate; absent or non-boolean keeps
      // the plain rect semantics.
      if (region.hitTest?.(x, y) !== false) {
        return true
      }
    }
  }

  return false
}

// React hook: register an interactive region for the lifetime of the
// component, deriving the rectangle from a ref's getBoundingClientRect().
// Pass `getRect` to override (e.g. the boot-failure overlay covers the full
// viewport). Pass `hitTest` to refine rect hits pixel-wise — callers must keep
// its reference stable, the region re-registers whenever it changes. Return
// `null` from `getRect` to opt out of the region for that frame.
export function useInteractiveRegion(
  id: string,
  ref: RefObject<HTMLElement | null>,
  getRect: (el: HTMLElement) => DOMRect | null = el => el.getBoundingClientRect(),
  hitTest?: (x: number, y: number) => boolean
): void {
  useEffect(() => {
    registerInteractiveRegion(
      id,
      () => {
        const el = ref.current

        return el ? getRect(el) : null
      },
      0,
      hitTest
    )

    return () => unregisterInteractiveRegion(id)
  }, [id, ref, getRect, hitTest])
}
