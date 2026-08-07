import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { awaitAvatarRegeneration, resolveAvatarRegeneration } from './avatar-regen-store'

describe('avatar-regen-store', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('resolves the awaiter when the event arrives after registration', async () => {
    const promise = awaitAvatarRegeneration('job-1')

    resolveAvatarRegeneration({ job_id: 'job-1', asset_url: 'https://x/p.png' })

    await expect(promise).resolves.toEqual({ job_id: 'job-1', asset_url: 'https://x/p.png' })
  })

  it('buffers late arrivals so an awaiter registered after the event still settles', async () => {
    resolveAvatarRegeneration({ job_id: 'job-2', error: 'provider down' })

    await expect(awaitAvatarRegeneration('job-2')).resolves.toEqual({ job_id: 'job-2', error: 'provider down' })

    // The buffered payload must be consumed exactly once.
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
    // First awaiter times out.
    const promise = awaitAvatarRegeneration('job-4')
    const settled = expect(promise).rejects.toThrow(/timed out/)
    await vi.advanceTimersByTime(120_001)
    await settled

    // Late event past the deadline: the tombstone must drop it, not re-buffer it.
    resolveAvatarRegeneration({ job_id: 'job-4', error: 'too late' })

    // A new awaiter for the same job starts fresh with its own timer.
    const retry = awaitAvatarRegeneration('job-4')
    const retrySettled = expect(retry).rejects.toThrow(/timed out/)
    await vi.advanceTimersByTime(120_001)
    await retrySettled
  })
})
