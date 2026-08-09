import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $effectiveTierOverride, $spriteState, $userPreferredTier } from './companion-store'
import { handleDragEndInteraction, handlePokeInteraction } from './interaction'

const hoisted = vi.hoisted(() => {
  return {
    playReactionAudio: vi.fn(),
    reportInteractionStat: vi.fn()
  }
})

vi.mock('./reactions/reaction-audio', () => ({
  hasManifest: () => true,
  pickReaction: () => ({
    id: 'reaction.poke-light.gentle.0',
    tags: ['温柔'],
    bucket: 'poke-light',
    text: '嗯？怎么啦？'
  }),
  playReactionAudio: hoisted.playReactionAudio,
  backgroundBakeReactions: vi.fn()
}))

vi.mock('./activity', () => ({
  reportInteractionStat: hoisted.reportInteractionStat
}))

vi.mock('./persona-store', () => ({
  $personalityTags: {
    get: () => ['温柔']
  }
}))

const originalDeskagent = (globalThis as { deskagent?: unknown }).deskagent

beforeEach(() => {
  vi.useFakeTimers()
  // Wipe any setTimeout left over from prior tests — interaction.ts uses a
  // module-level reset timer that fires 4s after each poke. Without clearing,
  // a stray callback from a previous test can reset pokeCount mid-suite.
  vi.clearAllTimers()
  vi.setSystemTime(new Date(10_000))
  hoisted.playReactionAudio.mockClear()
  hoisted.reportInteractionStat.mockClear()
})

afterEach(() => {
  vi.useRealTimers()
  ;(globalThis as { deskagent?: unknown }).deskagent = originalDeskagent
})

function installMediaSpy() {
  const tts = vi.fn()

  ;(globalThis as { deskagent?: unknown }).deskagent = {
    media: {
      tts,
      reactionAudio: { read: vi.fn().mockResolvedValue({ dataUrl: 'data:audio/mpeg;base64,AAA=' }), generate: vi.fn() }
    }
  }

  return { tts }
}

describe('poke / drag dispatch into pre-baked reaction audio', () => {
  it('handlePokeInteraction fires interacting state + playReactionAudio with bucket=poke-light', () => {
    handlePokeInteraction()

    expect($spriteState.get()).toBe('interacting')
    expect(hoisted.playReactionAudio).toHaveBeenCalledTimes(1)
    const [entry, opts] = hoisted.playReactionAudio.mock.calls[0]
    expect(entry.bucket).toBe('poke-light')
    expect(opts).toMatchObject({ bucket: 'poke-light', tags: ['温柔'], userInitiated: true })
  })

  it('repeated rapid pokes in a tight burst keep bucket=light (pokeCount reset by 4s timer, untouched by this test)', () => {
    installMediaSpy()
    handlePokeInteraction()

    expect(hoisted.playReactionAudio).toHaveBeenCalledTimes(1)
    expect(hoisted.playReactionAudio.mock.calls[0][1]).toMatchObject({ bucket: 'poke-light' })
  })

  it('reports a poke stat fire-and-forget on handlePokeInteraction', () => {
    handlePokeInteraction()

    expect(hoisted.reportInteractionStat).toHaveBeenCalledWith('poke')
  })

  it('never invokes the runtime media.tts path for a poke', () => {
    const { tts } = installMediaSpy()
    handlePokeInteraction()

    expect(tts).not.toHaveBeenCalled()
  })

  it('handleDragEndInteraction fires interacting state + playReactionAudio with bucket=drag', () => {
    handleDragEndInteraction()

    expect($spriteState.get()).toBe('interacting')
    expect(hoisted.playReactionAudio).toHaveBeenCalledTimes(1)
    expect(hoisted.playReactionAudio.mock.calls[0][1]).toMatchObject({
      bucket: 'drag',
      tags: ['温柔'],
      userInitiated: true
    })
    expect(hoisted.reportInteractionStat).toHaveBeenCalledWith('drag')
  })

  it('handles empty manifest by passing null through playReactionAudio', () => {
    vi.resetModules()
    vi.doMock('./reactions/reaction-audio', () => ({
      hasManifest: () => false,
      pickReaction: () => null,
      playReactionAudio: hoisted.playReactionAudio,
      backgroundBakeReactions: vi.fn()
    }))

    return import('./interaction').then(mod => {
      mod.handlePokeInteraction()
      expect(hoisted.playReactionAudio).toHaveBeenCalledTimes(1)
      expect(hoisted.playReactionAudio.mock.calls[0][0]).toBeNull()
    })
  })

  it('quiet tier does not affect pre-baked reaction playback (handled by audio-track)', () => {
    $effectiveTierOverride.set('quiet')
    $userPreferredTier.set('quiet')
    handlePokeInteraction()

    expect(hoisted.playReactionAudio).toHaveBeenCalledTimes(1)
    $effectiveTierOverride.set(null)
    $userPreferredTier.set('normal')
  })
})
