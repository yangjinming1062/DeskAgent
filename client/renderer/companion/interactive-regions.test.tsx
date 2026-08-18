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
    // Custom getRect returning a fixed viewport-sized rect. After mount,
    // isPointInteractive at any point inside the rect returns true; on
    // unmount the region is removed.
    const { unmount } = render(<RegionProbe getRect={() => new DOMRect(0, 0, 1920, 1080)} id="fullscreen" />)

    expect(isPointInteractive(500, 500)).toBe(true)
    expect(isPointInteractive(1919, 1079)).toBe(true)

    unmount()
    expect(isPointInteractive(500, 500)).toBe(false)
  })

  it('returns null from getRect when the ref is never attached', () => {
    // Render a component that calls the hook with a ref but never renders
    // an element to attach it to. The hook's getRect callback must return
    // null so isPointInteractive doesn't accidentally match anything.
    function Unbound() {
      const ref = useRef<HTMLDivElement>(null)
      useInteractiveRegion('unset-region', ref)

      return null
    }

    render(<Unbound />)
    expect(isPointInteractive(100, 100)).toBe(false)
  })

  it('unregisters on unmount', () => {
    // Sanity: even without a custom getRect, mounting + unmounting should
    // not leave a region behind that matches a hit-test.
    const { unmount } = render(<RegionProbe getRect={() => new DOMRect(0, 0, 10, 10)} id="ephemeral" />)

    expect(isPointInteractive(5, 5)).toBe(true)
    unmount()
    expect(isPointInteractive(5, 5)).toBe(false)
  })

  it('isPointInteractive honours a directly-registered region', () => {
    // Direct test of the underlying bucket — the hook above covers the
    // glue, this verifies the bucket itself behaves correctly.
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
    expect(isRegionHit('sprite-test', 110, 110)).toBe(false) // rejected by hitTest
    expect(isRegionHit('sprite-test', 20, 20)).toBe(false) // outside rect
    expect(isRegionHit('non-existent', 150, 150)).toBe(false)

    unregisterInteractiveRegion('sprite-test')
    unregisterInteractiveRegion('other-region')
    document.body.removeChild(div)
  })
})
