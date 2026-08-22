import * as THREE from 'three'
import { describe, expect, it, vi } from 'vitest'

import { Engine } from './Engine'

describe('Engine - Hitmap Lifecycle & Disposed Safety', () => {
  function createMockEngine(): Engine {
    const container = document.createElement('div')
    const canvas = document.createElement('canvas')
    container.appendChild(canvas)

    const mockRenderer = {
      clear: vi.fn(),
      dispose: vi.fn(),
      getContext: () => null,
      readRenderTargetPixels: vi.fn(),
      render: vi.fn(),
      setPixelRatio: vi.fn(),
      setRenderTarget: vi.fn(),
      setSize: vi.fn(),
      shadowMap: { enabled: false, type: THREE.PCFSoftShadowMap },
      toneMapping: THREE.ACESFilmicToneMapping,
      toneMappingExposure: 1.05
    } as unknown as THREE.WebGLRenderer

    // 通过原型实例化或通过强制访问私有构造函数
    const engine = Object.create(Engine.prototype) as Engine
    Object.assign(engine, {
      backendKind: 'classic-webgl',
      camera: new THREE.PerspectiveCamera(30, 1, 0.1, 100),
      canvas,
      character: { dispose: vi.fn(), root: new THREE.Group() },
      clock: new THREE.Clock(),
      disposed: false,
      hitMap: null,
      hitMapAt: 0,
      hitRefresh: null,
      hitRT: null,
      lighting: { dispose: vi.fn() },
      renderer: mockRenderer,
      running: false,
      scene: new THREE.Scene(),
      stats: { fps: 0 },
      stop: vi.fn()
    })

    return engine
  }

  it('Engine 销毁后调用 silhouetteHitmap() 直接返回 null，不触发任何渲染与回读', async () => {
    const engine = createMockEngine()
    const renderSpy = vi.spyOn(engine.renderer, 'render')

    engine.dispose()

    const result = await engine.silhouetteHitmap()
    expect(result).toBeNull()
    expect(renderSpy).not.toHaveBeenCalled()
  })
})
