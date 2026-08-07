import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $effectiveTierOverride, $spriteState, $userPreferredTier } from './companion-store'
import { handleDragEndInteraction, handlePokeInteraction } from './interaction'

const hoisted = vi.hoisted(() => {
  const request = vi.fn().mockResolvedValue({ threshold_met: false, peak_hour: 12 })

  return { request }
})

vi.mock('./proactive/proactive', () => ({
  speakProactive: vi.fn()
}))

vi.mock('@/shared/store/gateway', () => ({
  get $gateway() {
    return {
      get: () => ({ request: hoisted.request }),
      set: () => {}
    }
  }
}))

vi.mock('./chat-store', () => ({
  $chatOpen: { get: () => false, set: () => {} },
  setProactiveBubble: vi.fn()
}))

describe('interaction physical poke handling', () => {
  beforeEach(async () => {
    vi.useFakeTimers()
    // Advance the fake clock past the LLM enrichment throttle so a
    // previous test's ``lastLLMInteractAt`` (a module-level mutable) does
    // not starve the next poke's fetchLLMInteraction. Without this, the
    // quiet-tier test never reaches the quiet guard because the throttle
    // short-circuits the function first.
    vi.setSystemTime(new Date(10_000))
    hoisted.request.mockClear()
    const { speakProactive } = await import('./proactive/proactive')
    vi.mocked(speakProactive).mockClear()
    $effectiveTierOverride.set(null)
    $userPreferredTier.set('normal')
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('triggers interacting spriteState on handlePokeInteraction', async () => {
    const { speakProactive } = await import('./proactive/proactive')
    handlePokeInteraction()
    expect($spriteState.get()).toBe('interacting')
    expect(vi.mocked(speakProactive)).toHaveBeenCalledTimes(1)
    expect(vi.mocked(speakProactive).mock.calls[0][1]).toMatchObject({ userInitiated: true })
  })

  it('reports a poke stat fire-and-forget on every poke', () => {
    handlePokeInteraction()

    const pokeCalls = hoisted.request.mock.calls.filter(
      (c: unknown[]) => c[0] === 'companion.record_interaction_stats' && (c[1] as { kind?: string }).kind === 'poke'
    )

    expect(pokeCalls.length).toBeGreaterThanOrEqual(1)
  })

  it('does NOT call speakProactive a second time for the LLM enrichment', async () => {
    const { speakProactive } = await import('./proactive/proactive')
    hoisted.request.mockResolvedValue({ text: '嘿嘿被戳到啦～', emotion: 'happy', reason: '用户轻轻戳' })
    handlePokeInteraction()
    await vi.advanceTimersByTimeAsync(500)
    expect(vi.mocked(speakProactive)).toHaveBeenCalledTimes(1)
  })

  it('skips the LLM overlay when in effective quiet tier', async () => {
    $effectiveTierOverride.set('quiet')
    const { setProactiveBubble } = await import('./chat-store')
    vi.mocked(setProactiveBubble).mockClear()
    hoisted.request.mockResolvedValue({ text: '嘿', emotion: 'happy', reason: '' })
    handlePokeInteraction()
    await vi.advanceTimersByTimeAsync(500)
    expect(vi.mocked(setProactiveBubble)).not.toHaveBeenCalled()
  })

  it('reports a drag stat fire-and-forget on handleDragEndInteraction', () => {
    handleDragEndInteraction()

    const dragCalls = hoisted.request.mock.calls.filter(
      (c: unknown[]) => c[0] === 'companion.record_interaction_stats' && (c[1] as { kind?: string }).kind === 'drag'
    )

    expect(dragCalls.length).toBeGreaterThanOrEqual(1)
  })
})
