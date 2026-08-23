import type { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('./draco-loader', () => ({
  getDracoLoader: vi.fn(),
  __resetDracoLoaderForTest: vi.fn()
}))

import { getDracoLoader } from './draco-loader'
import { __resetGltfLoaderForTest, createGLTFLoader } from './gltf-loader-factory'

const mockedGetDraco = vi.mocked(getDracoLoader)

describe('createGLTFLoader 单例与 DRACO 延迟绑定', () => {
  afterEach(() => {
    __resetGltfLoaderForTest()
    vi.clearAllMocks()
  })

  it('多次调用返回同一个 GLTFLoader 实例引用', () => {
    mockedGetDraco.mockReturnValue(null)
    const a = createGLTFLoader()
    const b = createGLTFLoader()
    expect(a).toBe(b)
  })

  it('首次构造且 DRACO 未就绪时 dracoLoader 字段为 null', () => {
    mockedGetDraco.mockReturnValue(null)
    const loader = createGLTFLoader()
    expect((loader as unknown as { dracoLoader: DRACOLoader | null }).dracoLoader).toBeNull()
  })

  it('首次 DRACO 未就绪、后续就绪时再次调用能够成功补挂载 DRACOLoader', () => {
    mockedGetDraco.mockReturnValueOnce(null)
    const first = createGLTFLoader()
    expect((first as unknown as { dracoLoader: DRACOLoader | null }).dracoLoader).toBeNull()

    const fakeDraco = { type: 'fake-draco' } as unknown as DRACOLoader
    mockedGetDraco.mockReturnValue(fakeDraco)

    const second = createGLTFLoader()
    expect(second).toBe(first)
    expect((second as unknown as { dracoLoader: DRACOLoader | null }).dracoLoader).toBe(fakeDraco)
  })

  it('__resetGltfLoaderForTest 能够清空单例并在下次调用创建新实例', () => {
    mockedGetDraco.mockReturnValue(null)
    const first = createGLTFLoader()
    __resetGltfLoaderForTest()
    const second = createGLTFLoader()
    expect(first).not.toBe(second)
  })
})
