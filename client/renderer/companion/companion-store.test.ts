import { beforeEach, describe, expect, it, vi } from 'vitest'

describe('companion-store 安全加载与偏好档位', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.restoreAllMocks()
  })

  it('localStorage.getItem 抛 SecurityError 时不阻断模块加载并回退至 normal', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('SecurityError', 'SecurityError')
    })

    const mod = await import('./companion-store')
    expect(mod.$userPreferredTier.get()).toBe('normal')
  })

  it('localStorage.getItem 抛 QuotaExceededError 时安全回退至 normal', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('QuotaExceededError', 'QuotaExceededError')
    })

    const mod = await import('./companion-store')
    expect(mod.$userPreferredTier.get()).toBe('normal')
  })

  it('localStorage 存有合法值时正确恢复', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation((key: string) => {
      if (key === 'da.companion.disturbanceTier') {
        return 'quiet'
      }

      return null
    })

    const mod = await import('./companion-store')
    expect(mod.$userPreferredTier.get()).toBe('quiet')
  })

  it('localStorage 存有非法字符串时回退至 normal', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation((key: string) => {
      if (key === 'da.companion.disturbanceTier') {
        return 'unknown_tier'
      }

      return null
    })

    const mod = await import('./companion-store')
    expect(mod.$userPreferredTier.get()).toBe('normal')
  })

  it('setDisturbanceTier 能够更新 atom 并持久化', async () => {
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {})

    const mod = await import('./companion-store')
    mod.setDisturbanceTier('proactive')
    expect(mod.$userPreferredTier.get()).toBe('proactive')
    expect(setItemSpy).toHaveBeenCalledWith('da.companion.disturbanceTier', 'proactive')
  })
})
