import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $effectiveTierOverride, $spriteState, $userPreferredTier } from './companion-store'
import { handleDragEndInteraction, handlePokeInteraction } from './interaction'

const hoisted = vi.hoisted(() => {
  return {
    playReactionAudio: vi.fn(),
    reportInteractionStat: vi.fn(),
    gatewayRequest: vi.fn()
  }
})

vi.mock('@/shared/store/gateway', () => ({
  $gateway: {
    get: () => ({
      request: hoisted.gatewayRequest
    })
  }
}))

// Echoes the requested bucket back through the entry so the dispatch
// assertions below can tell poke-light from drag.
vi.mock('./reactions/reaction-audio', () => ({
  pickReaction: (bucket: string) => ({
    id: `reaction.${bucket}.gentle.0`,
    tags: ['温柔'],
    bucket,
    text: '嗯？怎么啦？'
  }),
  playReactionAudio: hoisted.playReactionAudio
}))

vi.mock('./activity', () => ({
  reportInteractionStat: hoisted.reportInteractionStat
}))

vi.mock('./persona-store', () => ({
  $personalityTags: {
    get: () => ['温柔']
  }
}))

beforeEach(() => {
  vi.useFakeTimers()
  // Wipe any setTimeout left over from prior tests — interaction.ts uses a
  // module-level reset timer that fires 4s after each poke. Without clearing,
  // a stray callback from a previous test can reset pokeCount mid-suite.
  vi.clearAllTimers()
  vi.setSystemTime(new Date(10_000))
  hoisted.playReactionAudio.mockClear()
  hoisted.reportInteractionStat.mockClear()
  hoisted.gatewayRequest.mockClear()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('poke / drag dispatch into reaction audio', () => {
  it('handlePokeInteraction fires interacting state + playReactionAudio with bucket=poke-light', () => {
    handlePokeInteraction()

    expect($spriteState.get()).toBe('interacting')
    expect(hoisted.playReactionAudio).toHaveBeenCalledTimes(1)
    expect(hoisted.playReactionAudio.mock.calls[0][0]).toMatchObject({ bucket: 'poke-light' })
  })

  it('repeated rapid pokes in a tight burst keep bucket=light (pokeCount reset by 4s timer, untouched by this test)', () => {
    handlePokeInteraction()

    expect(hoisted.playReactionAudio).toHaveBeenCalledTimes(1)
    expect(hoisted.playReactionAudio.mock.calls[0][0]).toMatchObject({ bucket: 'poke-light' })
  })

  it('reports a poke stat fire-and-forget on handlePokeInteraction', () => {
    handlePokeInteraction()

    expect(hoisted.reportInteractionStat).toHaveBeenCalledWith('poke')
  })

  it('handleDragEndInteraction plays local drag reaction and never issues RPC or reports stat', () => {
    handleDragEndInteraction()

    expect(hoisted.playReactionAudio).toHaveBeenCalledTimes(1)
    expect(hoisted.playReactionAudio.mock.calls[0][0]).toMatchObject({ bucket: 'drag' })
    expect(hoisted.reportInteractionStat).not.toHaveBeenCalled()
    expect(hoisted.gatewayRequest).not.toHaveBeenCalled()
  })

  it('handles empty manifest by passing null through playReactionAudio', () => {
    vi.resetModules()
    vi.doMock('./reactions/reaction-audio', () => ({
      pickReaction: () => null,
      playReactionAudio: hoisted.playReactionAudio
    }))

    return import('./interaction').then(mod => {
      mod.handlePokeInteraction()
      expect(hoisted.playReactionAudio).toHaveBeenCalledTimes(1)
      expect(hoisted.playReactionAudio.mock.calls[0][0]).toBeNull()
    })
  })

  it('quiet tier does not affect reaction playback (handled by audio-track)', () => {
    $effectiveTierOverride.set('quiet')
    $userPreferredTier.set('quiet')
    handlePokeInteraction()

    expect(hoisted.playReactionAudio).toHaveBeenCalledTimes(1)
    $effectiveTierOverride.set(null)
    $userPreferredTier.set('normal')
  })
})
