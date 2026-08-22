import * as THREE from 'three'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { Engine } from './Engine'

const captured = vi.hoisted(() => ({
  webgpu: [] as unknown[],
  webgl: [] as unknown[],
  webgpuInitShouldFail: false
}))

vi.mock('three/webgpu', () => {
  class MockWebGPUBackend {}

  return {
    WebGPUBackend: MockWebGPUBackend,
    WebGPURenderer: class MockWebGPURenderer {
      backend: unknown

      constructor(options: unknown) {
        captured.webgpu.push(options)
        this.backend = new MockWebGPUBackend()
      }

      async init(): Promise<void> {
        if (captured.webgpuInitShouldFail) {
          throw new Error('simulated WebGPU init failure')
        }
      }

      setPixelRatio(): void {}
      setSize(): void {}
      dispose(): void {}
    }
  }
})

vi.mock('three', async () => {
  const actual = await vi.importActual<typeof THREE>('three')

  class MockWebGLRenderer {
    constructor(options: unknown) {
      captured.webgl.push(options)
    }

    setPixelRatio(): void {}
    setSize(): void {}
    dispose(): void {}
  }

  return { ...actual, WebGLRenderer: MockWebGLRenderer }
})

// jsdom 无 WebGL 上下文；绕开 PMREM 真实渲染。
vi.mock('./LightingRig', () => ({
  LightingRig: class LightingRig {
    scene: THREE.Scene

    constructor(scene: THREE.Scene) {
      this.scene = scene
    }

    dispose(): void {}
  }
}))

describe('Engine.create — powerPreference 默认值与透传', () => {
  beforeEach(() => {
    captured.webgpu.length = 0
    captured.webgl.length = 0
    captured.webgpuInitShouldFail = false
  })

  function setupContainer(): HTMLElement {
    const container = document.createElement('div')
    document.body.appendChild(container)

    return container
  }

  async function createOnce(opts?: Parameters<typeof Engine.create>[1]): Promise<void> {
    const engine = await Engine.create(setupContainer(), opts)
    engine.dispose()
  }

  it('未传 powerPreference 时 WebGPU 与经典 WebGL 回退链均默认 low-power', async () => {
    await createOnce()
    expect(captured.webgpu[0]).toMatchObject({ powerPreference: 'low-power' })
    expect(captured.webgl).toHaveLength(0)

    captured.webgpuInitShouldFail = true
    await createOnce()
    expect(captured.webgpu).toHaveLength(2)
    expect(captured.webgl[0]).toMatchObject({ powerPreference: 'low-power' })
  })

  it('显式 high-performance 时透传给 WebGPU 与经典 WebGL 回退链', async () => {
    await createOnce({ powerPreference: 'high-performance' })
    expect(captured.webgpu[0]).toMatchObject({ powerPreference: 'high-performance' })

    captured.webgpuInitShouldFail = true
    await createOnce({ powerPreference: 'high-performance' })
    expect(captured.webgl[0]).toMatchObject({ powerPreference: 'high-performance' })
  })
})

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
