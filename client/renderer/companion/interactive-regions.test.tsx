import { render } from '@testing-library/react'
import { useRef } from 'react'
import { describe, expect, it } from 'vitest'

import {
  isPointInteractive,
  isRegionHit,
  registerInteractiveRegion,
  unregisterInteractiveRegion,
  useInteractiveRegion
} from './interactive-regions'

function RegionProbe({ id, getRect }: { id: string; getRect?: (el: HTMLElement) => DOMRect | null }) {
  const ref = useRef<HTMLDivElement>(null)
  useInteractiveRegion(id, ref, getRect)

  return <div data-testid="probe" ref={ref} />
}

describe('useInteractiveRegion', () => {
  it('registers the region through to isPointInteractive', () => {
    // 自定义 getRect 返回固定视口大小的矩形。挂载后，
    // 矩形内任意点的 isPointInteractive 返回 true；卸载后该区域被移除。
    const { unmount } = render(<RegionProbe getRect={() => new DOMRect(0, 0, 1920, 1080)} id="fullscreen" />)

    expect(isPointInteractive(500, 500)).toBe(true)
    expect(isPointInteractive(1919, 1079)).toBe(true)

    unmount()
    expect(isPointInteractive(500, 500)).toBe(false)
  })

  it('returns null from getRect when the ref is never attached', () => {
    // 渲染一个用 ref 调用 hook 但从未渲染元素去挂载它的组件。
    // hook 的 getRect 回调必须返回 null，避免 isPointInteractive 误命中。
    function Unbound() {
      const ref = useRef<HTMLDivElement>(null)
      useInteractiveRegion('unset-region', ref)

      return null
    }

    render(<Unbound />)
    expect(isPointInteractive(100, 100)).toBe(false)
  })

  it('unregisters on unmount', () => {
    // 健全性检查：即便没有自定义 getRect，挂载 + 卸载
    // 也不应在命中测试中遗留可命中的区域。
    const { unmount } = render(<RegionProbe getRect={() => new DOMRect(0, 0, 10, 10)} id="ephemeral" />)

    expect(isPointInteractive(5, 5)).toBe(true)
    unmount()
    expect(isPointInteractive(5, 5)).toBe(false)
  })

  it('isPointInteractive honours a directly-registered region', () => {
    // 直接测试底层桶——上面的 hook 已覆盖胶水代码，
    // 这里验证桶本身行为正确。
    const div = document.createElement('div')
    document.body.appendChild(div)

    div.getBoundingClientRect = () =>
      ({ left: 100, top: 100, right: 150, bottom: 150, width: 50, height: 50, x: 100, y: 100, toJSON() {} }) as DOMRect

    registerInteractiveRegion('manual', () => div.getBoundingClientRect())
    expect(isPointInteractive(120, 120)).toBe(true)
    expect(isPointInteractive(0, 0)).toBe(false)

    unregisterInteractiveRegion('manual')
    document.body.removeChild(div)
  })

  it('isRegionHit checks only the targeted region with hitTest predicate', () => {
    const div = document.createElement('div')
    document.body.appendChild(div)

    div.getBoundingClientRect = () =>
      ({
        left: 100,
        top: 100,
        right: 200,
        bottom: 200,
        width: 100,
        height: 100,
        x: 100,
        y: 100,
        toJSON() {}
      }) as DOMRect

    const hitTest = (x: number, y: number) => x >= 120 && y >= 120

    registerInteractiveRegion('sprite-test', () => div.getBoundingClientRect(), 0, hitTest)
    registerInteractiveRegion('other-region', () => new DOMRect(0, 0, 50, 50))

    expect(isRegionHit('sprite-test', 150, 150)).toBe(true)
    expect(isRegionHit('sprite-test', 110, 110)).toBe(false) // 被 hitTest 拒绝
    expect(isRegionHit('sprite-test', 20, 20)).toBe(false) // 在矩形外
    expect(isRegionHit('non-existent', 150, 150)).toBe(false)

    unregisterInteractiveRegion('sprite-test')
    unregisterInteractiveRegion('other-region')
    document.body.removeChild(div)
  })
})
