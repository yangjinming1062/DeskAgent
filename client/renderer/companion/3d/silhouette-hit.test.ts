import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as interactiveRegions from '@/companion/interactive-regions'

import type { SilhouetteHitmap } from './Engine'
import { $sprite3DHitTest, attachSilhouetteHitProbe } from './silhouette-hit'

describe('silhouette-hit', () => {
  let canvas: HTMLCanvasElement
  let probeMock: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    $sprite3DHitTest.set(null)
    probeMock = vi.spyOn(interactiveRegions, 'probeInteractiveRegions').mockImplementation(() => {})
    canvas = document.createElement('canvas')
    canvas.getBoundingClientRect = () =>
      ({
        bottom: 400,
        height: 300,
        left: 100,
        right: 300,
        top: 100,
        width: 200,
        x: 100,
        y: 100,
        toJSON() {}
      }) as DOMRect
  })

  it('returns null before hitmap is loaded (falling back to bounding rect)', () => {
    let resolveHitmap!: (map: SilhouetteHitmap | null) => void

    const hitmapPromise = new Promise<SilhouetteHitmap | null>(resolve => {
      resolveHitmap = resolve
    })

    const detach = attachSilhouetteHitProbe({
      canvas,
      silhouetteHitmap: () => hitmapPromise
    })

    const test = $sprite3DHitTest.get()
    expect(test).toBeTypeOf('function')
    // Before hitmap resolves, test returns null so SpriteStage uses rect fallback
    expect(test!(150, 150)).toBeNull()

    resolveHitmap(null)
    detach()
  })

  it('synchronously tests coordinates against the latest hitmap in O(1)', async () => {
    // 2x2 top-down hitmap:
    // py=0 (top row): [0, 255] (top-left transparent, top-right opaque)
    // py=1 (bottom row): [255, 0] (bottom-left opaque, bottom-right transparent)
    const alpha = new Uint8Array([
      0,
      255, // row 0 (top)
      255,
      0 // row 1 (bottom)
    ])

    const hitmap: SilhouetteHitmap = { alpha, height: 2, width: 2 }

    const detach = attachSilhouetteHitProbe({
      canvas,
      silhouetteHitmap: () => Promise.resolve(hitmap)
    })

    // Wait for the initial eager refresh to resolve
    await Promise.resolve()
    await Promise.resolve()

    const test = $sprite3DHitTest.get()
    expect(test).toBeTypeOf('function')
    expect(probeMock).toHaveBeenCalled()

    // Canvas rect: left=100, top=100, width=200, height=300
    // Test top-left quadrant (clientX=120, clientY=120): relX=0.1 -> px=0, relY=0.067 -> py=0 -> alpha[0*2+0] = 0 (miss)
    expect(test!(120, 120)).toBe(false)

    // Test top-right quadrant (clientX=280, clientY=120): relX=0.9 -> px=1, relY=0.067 -> py=0 -> alpha[0*2+1] = 255 (hit)
    expect(test!(280, 120)).toBe(true)

    // Test bottom-left quadrant (clientX=120, clientY=380): relX=0.1 -> px=0, relY=0.933 -> py=1 -> alpha[1*2+0] = 255 (hit)
    expect(test!(120, 380)).toBe(true)

    // Test bottom-right quadrant (clientX=280, clientY=380): relX=0.9 -> px=1, relY=0.933 -> py=1 -> alpha[1*2+1] = 0 (miss)
    expect(test!(280, 380)).toBe(false)

    // Points outside canvas rect return false
    expect(test!(50, 50)).toBe(false)
    expect(test!(350, 350)).toBe(false)

    detach()
    expect($sprite3DHitTest.get()).toBeNull()
  })

  it('triggers hitmap refresh on mouse movement near canvas', async () => {
    let callCount = 0
    const alpha = new Uint8Array([255])
    const hitmap: SilhouetteHitmap = { alpha, height: 1, width: 1 }

    const detach = attachSilhouetteHitProbe({
      canvas,
      silhouetteHitmap: () => {
        callCount++

        return Promise.resolve(hitmap)
      }
    })

    // Initial eager refresh
    expect(callCount).toBe(1)

    // Mouse move near canvas (rect: 100-300 x 100-400)
    window.dispatchEvent(new MouseEvent('mousemove', { clientX: 150, clientY: 200 }))

    // Move outside margin (margin is 50px)
    window.dispatchEvent(new MouseEvent('mousemove', { clientX: 10, clientY: 10 }))

    detach()
  })

  it('reads hitmap directly from engine.getSilhouetteHitmap when provided', () => {
    const alpha = new Uint8Array([255])
    const hitmap: SilhouetteHitmap = { alpha, height: 1, width: 1 }

    const detach = attachSilhouetteHitProbe({
      canvas,
      getSilhouetteHitmap: () => hitmap,
      silhouetteHitmap: () => Promise.resolve(hitmap)
    })

    const test = $sprite3DHitTest.get()
    expect(test).toBeTypeOf('function')
    expect(test!(150, 150)).toBe(true)

    detach()
  })
})
