import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { $gateway } from '@/shared/store/gateway'
import type { $runnerPhase } from '@/shared/store/runner-status'

import type { $focusContext, $lastIdleSeconds, $screenLocked } from './activity'
import type { consultAutonomyLLM, startAutonomyProvision, stopAutonomyProvision } from './autonomy'
import type { $spriteState } from './companion-store'
import type { $llmAutonomy } from './prefs'

describe('autonomy provision loop and consultAutonomyLLM', () => {
  let mockRequest: ReturnType<typeof vi.fn>
  let consult: typeof consultAutonomyLLM
  let start: typeof startAutonomyProvision
  let stop: typeof stopAutonomyProvision
  let gateway: typeof $gateway
  let runnerPhase: typeof $runnerPhase
  let focusContext: typeof $focusContext
  let lastIdleSeconds: typeof $lastIdleSeconds
  let screenLocked: typeof $screenLocked
  let spriteState: typeof $spriteState
  let llmAutonomy: typeof $llmAutonomy

  beforeEach(async () => {
    // Each test gets a fresh module so the module-level throttle / snapshot
    // counters don't bleed across tests.
    vi.resetModules()
    vi.useFakeTimers()

    const gatewayModule = await import('@/shared/store/gateway')
    const runnerStatusModule = await import('@/shared/store/runner-status')
    const activityModule = await import('./activity')
    const companionStoreModule = await import('./companion-store')
    const prefsModule = await import('./prefs')
    const autonomyModule = await import('./autonomy')

    gateway = gatewayModule.$gateway
    runnerPhase = runnerStatusModule.$runnerPhase
    focusContext = activityModule.$focusContext
    lastIdleSeconds = activityModule.$lastIdleSeconds
    screenLocked = activityModule.$screenLocked
    spriteState = companionStoreModule.$spriteState
    llmAutonomy = prefsModule.$llmAutonomy
    consult = autonomyModule.consultAutonomyLLM
    start = autonomyModule.startAutonomyProvision
    stop = autonomyModule.stopAutonomyProvision

    llmAutonomy.set(true)
    runnerPhase.set('running')
    lastIdleSeconds.set(0)
    screenLocked.set(false)
    focusContext.set(null)

    mockRequest = vi.fn().mockResolvedValue({ should_act: false, action: 'stay' })
    gateway.set({
      request: mockRequest
    } as unknown as typeof gateway.value)
  })

  afterEach(() => {
    stop()
    vi.useRealTimers()
    gateway.set(null)
  })

  it('does NOT consult LLM when $llmAutonomy is false', async () => {
    llmAutonomy.set(false)
    await consult(true)
    expect(mockRequest).not.toHaveBeenCalled()
  })

  it('consults LLM when forced or state changes', async () => {
    await consult(true)
    expect(mockRequest).toHaveBeenCalledTimes(1)
    expect(mockRequest.mock.calls[0][0]).toBe('companion.should_act')

    // Second call immediately without state change or force should be skipped due to 60s minimum interval
    await consult(false)
    expect(mockRequest).toHaveBeenCalledTimes(1)
  })

  it('respects 60s minimum consult interval even on state change', async () => {
    await consult(true)
    expect(mockRequest).toHaveBeenCalledTimes(1)

    // Advance 10s, change state
    vi.advanceTimersByTime(10_000)
    screenLocked.set(true)
    await consult(false)
    expect(mockRequest).toHaveBeenCalledTimes(1) // throttled

    // Advance to 61s total
    vi.advanceTimersByTime(51_000)
    await consult(false)
    expect(mockRequest).toHaveBeenCalledTimes(2)
  })

  it('executes allowed action "go_sleep" when should_act is true', async () => {
    mockRequest.mockResolvedValueOnce({ should_act: true, action: 'go_sleep', reason: 'bedtime' })

    await consult(true)
    expect(mockRequest).toHaveBeenCalledTimes(1)
    expect(spriteState.get()).toBe('sleeping')
  })

  it('ignores invalid action names when should_act is true', async () => {
    mockRequest.mockResolvedValueOnce({ should_act: true, action: 'invalid_dance', reason: 'fun' })
    const stateBefore = spriteState.get()

    await consult(true)
    expect(spriteState.get()).toBe(stateBefore)
  })

  it('startAutonomyProvision subscribes to state changes and background timer', async () => {
    start()
    expect(mockRequest).toHaveBeenCalledTimes(1) // initial consult on subscribe

    // Background timer (30 mins)
    vi.advanceTimersByTime(30 * 60_000 + 1000)
    expect(mockRequest).toHaveBeenCalledTimes(2)

    stop()
  })
})
