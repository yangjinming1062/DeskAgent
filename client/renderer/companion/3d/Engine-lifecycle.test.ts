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

    // 通过强制访问私有构造函数实例化
    const engine = new (Engine as unknown as new (
      renderer: unknown,
      backendKind: string,
      canvas: HTMLCanvasElement
    ) => Engine)(mockRenderer, 'classic-webgl', canvas)

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

  it('Engine.tick 单次或少量瞬态错误不停止 ticker，连续 5 次错误才彻底停止', () => {
    const engine = createMockEngine()
    Object.assign(engine, {
      character: { update: vi.fn(), root: new THREE.Group() },
      running: true,
      lastFrameAt: 0,
      profile: 'active'
    })

    const tick = (engine as unknown as { tick: () => void }).tick
    const renderMock = engine.renderer.render as unknown as ReturnType<typeof vi.fn>

    // 阶段 1：连续 4 次 render 抛错——计数器递增但 ticker 不停
    renderMock.mockImplementation(() => {
      throw new Error('transient WebGL glitch')
    })

    for (let i = 0; i < 4; i++) {
      ;(engine as unknown as { lastFrameAt: number }).lastFrameAt = 0
      tick()
      expect((engine as unknown as { running: boolean }).running).toBe(true)
      expect((engine as unknown as { consecutiveErrors: number }).consecutiveErrors).toBe(i + 1)
    }

    // 阶段 2：一次成功 render 应当重置计数器
    renderMock.mockImplementation(() => {})
    ;(engine as unknown as { lastFrameAt: number }).lastFrameAt = 0
    tick()
    expect((engine as unknown as { running: boolean }).running).toBe(true)
    expect((engine as unknown as { consecutiveErrors: number }).consecutiveErrors).toBe(0)

    // 阶段 3：再次连续 5 次错误才彻底停转
    renderMock.mockImplementation(() => {
      throw new Error('fatal WebGL crash')
    })

    for (let i = 0; i < 4; i++) {
      ;(engine as unknown as { lastFrameAt: number }).lastFrameAt = 0
      tick()
      expect((engine as unknown as { running: boolean }).running).toBe(true)
    }

    ;(engine as unknown as { lastFrameAt: number }).lastFrameAt = 0
    tick()
    expect((engine as unknown as { running: boolean }).running).toBe(false)
  })
})
