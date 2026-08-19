import { type RefObject, useEffect } from 'react'

export type InteractiveRegion = {
  getRect: () => DOMRect | null
  hitTest?: (x: number, y: number) => boolean
  id: string
}

// 按窗口分桶的 map，使每个窗口各自管理自己的交互区域——
// 第二个窗口（各自的 SpriteStage）不会冲掉共享列表。
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

/** Re-run the window's capture probe outside the mousemove path — e.g. an
 * async hit refinement just landed for a stationary cursor. */
export function probeInteractiveRegions(windowId: number = 0): void {
  _probesByWindow.get(windowId)?.()
}

export function isPointInteractive(x: number, y: number, windowId: number = 0): boolean {
  const regions = _bucket(windowId)

  for (const region of regions.values()) {
    const rect = region.getRect()

    if (!rect) {
      continue
    }

    if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) {
      // 用像素谓词细化矩形命中；缺失或非布尔值时保持纯矩形语义。
      if (region.hitTest?.(x, y) !== false) {
        return true
      }
    }
  }

  return false
}

export function isRegionHit(id: string, x: number, y: number, windowId: number = 0): boolean {
  const regions = _bucket(windowId)
  const region = regions.get(id)

  if (!region) {
    return false
  }

  const rect = region.getRect()

  if (!rect) {
    return false
  }

  if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) {
    return region.hitTest?.(x, y) !== false
  }

  return false
}

// React hook：在组件生命周期内注册一个交互区域，
// 通过 ref 的 getBoundingClientRect() 派生矩形。
// 传 `getRect` 可覆盖默认行为（如 boot-failure 覆盖层覆盖整个视口）。
// 传 `hitTest` 可按像素细化命中——调用方必须保持其引用稳定，
// 引用变化时区域会重新注册。在 `getRect` 中返回 `null` 可让该帧退出区域。
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
