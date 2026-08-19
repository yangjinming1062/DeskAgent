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
    // 命中图未解析时返回 null，让 SpriteStage 走矩形回退
    expect(test!(150, 150)).toBeNull()

    resolveHitmap(null)
    detach()
  })

  it('同步以 O(1) 时间把坐标命中到最新的命中图', async () => {
    // 2x2 自顶向下命中图：
    // py=0（顶行）：[0, 255]（左上透明、右上不透明）
    // py=1（底行）：[255, 0]（左下不透明、右下透明）
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

    // 等待初始的预热刷新解析完成
    await Promise.resolve()
    await Promise.resolve()

    const test = $sprite3DHitTest.get()
    expect(test).toBeTypeOf('function')
    expect(probeMock).toHaveBeenCalled()

    // 画布矩形：left=100、top=100、width=200、height=300
    // 测试左上象限（clientX=120、clientY=120）：relX=0.1 -> px=0，relY=0.067 -> py=0 -> alpha[0*2+0] = 0（未命中）
    expect(test!(120, 120)).toBe(false)

    // 测试右上象限（clientX=280、clientY=120）：relX=0.9 -> px=1，relY=0.067 -> py=0 -> alpha[0*2+1] = 255（命中）
    expect(test!(280, 120)).toBe(true)

    // 测试左下象限（clientX=120、clientY=380）：relX=0.1 -> px=0，relY=0.933 -> py=1 -> alpha[1*2+0] = 255（命中）
    expect(test!(120, 380)).toBe(true)

    // 测试右下象限（clientX=280、clientY=380）：relX=0.9 -> px=1，relY=0.933 -> py=1 -> alpha[1*2+1] = 0（未命中）
    expect(test!(280, 380)).toBe(false)

    // 画布矩形之外的点返回 false
    expect(test!(50, 50)).toBe(false)
    expect(test!(350, 350)).toBe(false)

    detach()
    expect($sprite3DHitTest.get()).toBeNull()
  })

  it('在画布附近移动鼠标时触发命中图刷新', async () => {
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

    // 初始的预热刷新
    expect(callCount).toBe(1)

    // 在画布附近移动鼠标（矩形：100-300 × 100-400）
    window.dispatchEvent(new MouseEvent('mousemove', { clientX: 150, clientY: 200 }))

    // 移动到余量之外（余量为 50 像素）
    window.dispatchEvent(new MouseEvent('mousemove', { clientX: 10, clientY: 10 }))

    detach()
  })

  it('当传入时直接从 engine.getSilhouetteHitmap 读取命中图', () => {
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
