import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { awaitAvatarRegeneration, resolveAvatarRegeneration } from './avatar-regen-store'

describe('avatar-regen-store', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('resolves the avatar awaiter when the event arrives after registration', async () => {
    const promise = awaitAvatarRegeneration('job-1')

    resolveAvatarRegeneration({ job_id: 'job-1', asset_url: 'https://x/p.png' })

    await expect(promise).resolves.toEqual({ job_id: 'job-1', asset_url: 'https://x/p.png' })
  })

  it('buffers late arrivals so an awaiter registered after the event still settles', async () => {
    resolveAvatarRegeneration({ job_id: 'job-2', error: 'provider down' })

    await expect(awaitAvatarRegeneration('job-2')).resolves.toEqual({ job_id: 'job-2', error: 'provider down' })

    // 缓冲的载荷只能被消费一次。
    resolveAvatarRegeneration({ job_id: 'job-2', error: 'second' })
    await expect(awaitAvatarRegeneration('job-2')).resolves.toEqual({ job_id: 'job-2', error: 'second' })
  })

  it('rejects after the timeout so the UI does not stay pending forever', async () => {
    const promise = awaitAvatarRegeneration('job-3')
    const expectation = expect(promise).rejects.toThrow(/timed out/)

    await vi.advanceTimersByTime(120_001)
    await expectation
  })

  it('drops a late event that arrives after the timeout', async () => {
    // 第一个 awaiter 超时。
    const promise = awaitAvatarRegeneration('job-4')
    const settled = expect(promise).rejects.toThrow(/timed out/)
    await vi.advanceTimersByTime(120_001)
    await settled

    // 超时后才到达的迟到事件：墓碑记录必须丢弃它，而不是再次入队。
    resolveAvatarRegeneration({ job_id: 'job-4', error: 'too late' })

    // 同一个 job 的新 awaiter 从零开始计时。
    const retry = awaitAvatarRegeneration('job-4')
    const retrySettled = expect(retry).rejects.toThrow(/timed out/)
    await vi.advanceTimersByTime(120_001)
    await retrySettled
  })
})
