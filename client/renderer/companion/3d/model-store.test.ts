import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { log } from '@/shared/lib/log'

import {
  $modelGenError,
  $modelGenState,
  $modelInfo,
  $modelRetryable,
  $modelRetryModelId,
  clearModelRetry,
  ensureModelGeneration,
  hydrateModel,
  setModelFailed
} from './model-store'

const baseModelResponse = {
  id: 7,
  asset_url: 'http://localhost:8000/api/companion/model/file/1/abc.glb?expires=1&sig=1',
  provider: 'base_texture',
  species: '精灵',
  status: 'succeeded',
  rig_type: 'biped',
  rig_naming: 'tripo',
  has_rig: true,
  content_hash: 'sha256_mock_hash_123'
}

function setWindowSpiritagent(api: ReturnType<typeof vi.fn>): void {
  ;(window as { spiritagent?: unknown }).spiritagent = { api }
}

function restoreWindowSpiritagent(): void {
  delete (window as { spiritagent?: unknown }).spiritagent
}

describe('hydrateModel', () => {
  beforeEach(() => {
    $modelInfo.set({
      id: null,
      asset_url: null,
      species: null,
      provider: null,
      has_rig: false,
      status: 'pending',
      rig_type: 'biped',
      rig_naming: 'tripo',
      style: 'realistic',
      content_hash: null
    })
    vi.spyOn(log, 'warn').mockImplementation(() => undefined)
  })

  afterEach(() => {
    vi.restoreAllMocks()
    restoreWindowSpiritagent()
  })

  it('在 200 响应上发布当前模型', async () => {
    const api = vi.fn().mockResolvedValue(baseModelResponse)
    setWindowSpiritagent(api)

    await hydrateModel()

    expect(api).toHaveBeenCalledWith({ path: '/api/companion/model' })
    // 成功路径也只读——hydrateModel 不负责触发新生成
    expect(api).not.toHaveBeenCalledWith({ path: '/api/companion/model', method: 'POST', body: {} })
    expect($modelInfo.get()).toEqual({
      id: 7,
      asset_url: baseModelResponse.asset_url,
      species: '精灵',
      provider: 'base_texture',
      has_rig: true,
      status: 'succeeded',
      rig_type: 'biped',
      rig_naming: 'tripo',
      style: 'realistic',
      content_hash: 'sha256_mock_hash_123'
    })
    expect(log.warn).not.toHaveBeenCalled()
  })

  it('把 404 当作正常情况（没有激活行），不触发新生成——3D 生成由 confirm-front 显式触发', async () => {
    $modelInfo.set({ ...$modelInfo.get(), species: '人类' })
    const before = $modelInfo.get()

    const api = vi.fn().mockImplementation(async req => {
      if (req.path === '/api/companion/model' && req.method !== 'POST') {
        throw new Error('404 /api/companion/model')
      }

      return { id: 1, status: 'generating' }
    })

    setWindowSpiritagent(api)

    await hydrateModel()

    expect($modelInfo.get()).toEqual(before)
    expect(api).toHaveBeenCalledWith({ path: '/api/companion/model' })
    // 关键断言:404 时 hydrateModel 只读不写,不调 POST /api/companion/model
    expect(api).not.toHaveBeenCalledWith({ path: '/api/companion/model', method: 'POST', body: {} })
    expect(log.warn).not.toHaveBeenCalled()
  })

  it('在 5xx 时打 warn，让模型缺失可被定位', async () => {
    const api = vi.fn().mockImplementation(async req => {
      if (req.path === '/api/companion/model' && req.method !== 'POST') {
        throw new Error('500 /api/companion/model: boom')
      }

      return { id: 1, status: 'generating' }
    })

    setWindowSpiritagent(api)

    await hydrateModel()

    expect($modelInfo.get().status).toBe('pending')
    expect(log.warn).toHaveBeenCalledWith('model-store', 'hydrateModel failed', expect.any(Error))
    // 同样:5xx 时也不应该触发 POST
    expect(api).not.toHaveBeenCalledWith({ path: '/api/companion/model', method: 'POST', body: {} })
  })
})

describe('download-failure retry state', () => {
  beforeEach(() => {
    $modelGenState.set('idle')
    $modelGenError.set(null)
    clearModelRetry()
    vi.spyOn(log, 'warn').mockImplementation(() => undefined)
  })

  afterEach(() => {
    vi.restoreAllMocks()
    restoreWindowSpiritagent()
  })

  it('setModelFailed 原子地记录可重试状态与模型 id', () => {
    setModelFailed('生成失败')
    expect($modelGenState.get()).toBe('failed')
    expect($modelRetryable.get()).toBe(false)
    expect($modelRetryModelId.get()).toBeNull()

    setModelFailed('下载失败，可重试下载', { retryDownload: true, modelId: 3 })
    expect($modelGenError.get()).toBe('下载失败，可重试下载')
    expect($modelRetryable.get()).toBe(true)
    expect($modelRetryModelId.get()).toBe(3)
  })

  it('把 download_failed 的 POST 响应映射到 failed + 可重试状态', async () => {
    const api = vi.fn().mockResolvedValue({ id: 9, status: 'download_failed' })
    setWindowSpiritagent(api)

    await ensureModelGeneration()

    expect(api).toHaveBeenCalled()
    expect($modelGenState.get()).toBe('failed')
    expect($modelRetryable.get()).toBe(true)
    expect($modelRetryModelId.get()).toBe(9)
  })

  it('在真正请求生成时保持状态不变', async () => {
    const api = vi.fn().mockResolvedValue({ id: 10, status: 'generating' })
    setWindowSpiritagent(api)

    await ensureModelGeneration()

    expect($modelGenState.get()).toBe('idle')
    expect($modelRetryable.get()).toBe(false)
  })
})
