import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $gateway } from '@/shared/store/gateway'
import { $runnerPhase } from '@/shared/store/runner-status'

import { $focusContext, $lastIdleSeconds, $screenLocked } from './activity'
import { consultAutonomyLLM, resetAutonomyState, startAutonomyProvision, stopAutonomyProvision } from './autonomy'
import { $spriteState } from './companion-store'
import { $llmAutonomy } from './prefs'

describe('autonomy provision loop and consultAutonomyLLM', () => {
  let mockRequest: ReturnType<typeof vi.fn>

  beforeEach(() => {
    vi.useFakeTimers()
    resetAutonomyState()
    $llmAutonomy.set(true)
    $runnerPhase.set('running')
    $lastIdleSeconds.set(0)
    $screenLocked.set(false)
    $focusContext.set(null)

    mockRequest = vi.fn().mockResolvedValue({ should_act: false, action: 'stay' })
    $gateway.set({
      request: mockRequest
    } as unknown as typeof $gateway.value)
  })

  afterEach(() => {
    stopAutonomyProvision()
    resetAutonomyState()
    vi.useRealTimers()
    $gateway.set(null)
  })

  it('does NOT consult LLM when $llmAutonomy is false', async () => {
    $llmAutonomy.set(false)
    await consultAutonomyLLM(true)
    expect(mockRequest).not.toHaveBeenCalled()
  })

  it('consults LLM when forced or state changes', async () => {
    await consultAutonomyLLM(true)
    expect(mockRequest).toHaveBeenCalledTimes(1)
    expect(mockRequest.mock.calls[0][0]).toBe('companion.should_act')

    // Second call immediately without state change or force should be skipped due to 60s minimum interval
    await consultAutonomyLLM(false)
    expect(mockRequest).toHaveBeenCalledTimes(1)
  })

  it('respects 60s minimum consult interval even on state change', async () => {
    await consultAutonomyLLM(true)
    expect(mockRequest).toHaveBeenCalledTimes(1)

    // Advance 10s, change state
    vi.advanceTimersByTime(10_000)
    $screenLocked.set(true)
    await consultAutonomyLLM(false)
    expect(mockRequest).toHaveBeenCalledTimes(1) // throttled

    // Advance to 61s total
    vi.advanceTimersByTime(51_000)
    await consultAutonomyLLM(false)
    expect(mockRequest).toHaveBeenCalledTimes(2)
  })

  it('executes allowed action "go_sleep" when should_act is true', async () => {
    mockRequest.mockResolvedValueOnce({ should_act: true, action: 'go_sleep', reason: 'bedtime' })

    await consultAutonomyLLM(true)
    expect(mockRequest).toHaveBeenCalledTimes(1)
    expect($spriteState.get()).toBe('sleeping')
  })

  it('ignores invalid action names when should_act is true', async () => {
    mockRequest.mockResolvedValueOnce({ should_act: true, action: 'invalid_dance', reason: 'fun' })
    const stateBefore = $spriteState.get()

    await consultAutonomyLLM(true)
    expect($spriteState.get()).toBe(stateBefore)
  })

  it('startAutonomyProvision subscribes to state changes and background timer', async () => {
    startAutonomyProvision()
    expect(mockRequest).toHaveBeenCalledTimes(1) // initial consult on subscribe

    // Background timer (30 mins)
    vi.advanceTimersByTime(30 * 60_000 + 1000)
    expect(mockRequest).toHaveBeenCalledTimes(2)

    stopAutonomyProvision()
  })
})
