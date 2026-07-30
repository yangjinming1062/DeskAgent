import { beforeEach, describe, expect, it, vi } from 'vitest'

import { $spriteState } from './companion-store'
import { handlePokeInteraction } from './interaction'

vi.mock('./proactive/proactive', () => ({
  speakProactive: vi.fn()
}))

describe('interaction physical poke handling', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  it('triggers interacting spriteState on handlePokeInteraction', () => {
    handlePokeInteraction()
    expect($spriteState.get()).toBe('interacting')
  })
})
