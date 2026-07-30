import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  $previousState,
  $spriteEmotion,
  $spriteState,
  setSpriteState,
  type SpriteEmotion,
  type SpriteStateName
} from './companion-store'

describe('companion-store Phase 2 state machine', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    setSpriteState('idle', { force: true })
  })

  it('allows high priority state to interrupt lower priority state', () => {
    setSpriteState('thinking')
    expect($spriteState.get()).toBe('thinking')

    setSpriteState('working')
    expect($spriteState.get()).toBe('working')
  })

  it('prevents lower priority state from interrupting higher priority state', () => {
    setSpriteState('working')
    expect($spriteState.get()).toBe('working')

    // listening is priority 40 vs working priority 70
    setSpriteState('listening')
    expect($spriteState.get()).toBe('working')
  })

  it('handles transient emotional state and reverts to previous state after timer', () => {
    setSpriteState('working')
    expect($spriteState.get()).toBe('working')

    setSpriteState('emotional', { emotion: 'happy', durationMs: 1000 })
    expect($spriteState.get()).toBe('emotional')
    expect($spriteEmotion.get()).toBe('happy')
    expect($previousState.get()).toBe('working')

    vi.advanceTimersByTime(1000)
    expect($spriteState.get()).toBe('working')
    expect($spriteEmotion.get()).toBeNull()
  })

  it('handles transient interacting state', () => {
    setSpriteState('listening')
    setSpriteState('interacting', { durationMs: 500 })
    expect($spriteState.get()).toBe('interacting')

    vi.advanceTimersByTime(500)
    expect($spriteState.get()).toBe('listening')
  })
})
