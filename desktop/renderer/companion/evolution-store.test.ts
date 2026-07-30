import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  $evolutionLevel,
  $intimacyScore,
  $totalInteractions,
  recordInteraction
} from './evolution-store'

vi.mock('./proactive/proactive', () => ({
  speakProactive: vi.fn()
}))

describe('evolution-store intimacy and growth mechanism', () => {
  beforeEach(() => {
    $intimacyScore.set(0)
    $evolutionLevel.set(1)
    $totalInteractions.set(0)
  })

  it('increments total interactions and intimacy score', () => {
    recordInteraction('poke')
    expect($totalInteractions.get()).toBe(1)
    expect($intimacyScore.get()).toBe(1)
    expect($evolutionLevel.get()).toBe(1)
  })

  it('levels up when intimacy score reaches threshold', () => {
    for (let i = 0; i < 15; i++) {
      recordInteraction('poke')
    }
    expect($intimacyScore.get()).toBe(15)
    expect($evolutionLevel.get()).toBe(2)
  })
})
