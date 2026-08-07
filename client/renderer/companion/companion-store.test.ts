import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  $effectiveTier,
  $effectiveTierOverride,
  $previousState,
  $spriteEmotion,
  $spriteState,
  $userPreferredTier,
  reportUserActivity,
  setDisturbanceTier,
  setSpriteState
} from './companion-store'

describe('companion-store Phase 2 state machine', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    setSpriteState('idle', { force: true })
    $userPreferredTier.set('normal')
    $effectiveTierOverride.set(null)
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

  it('returns to idle after 10s of inactivity while in working', () => {
    setSpriteState('idle', { force: true })
    expect($spriteState.get()).toBe('idle')

    // 6 consecutive activity ticks flip idle -> working (counter gate).
    for (let i = 0; i < 6; i++) {
      reportUserActivity()
    }

    expect($spriteState.get()).toBe('working')

    // Working (pri 70) gates idle (pri 10) — without force, the 10s
    // inactivity timer would expire but the state would stay locked on
    // the working badge. The fix forces the exit.
    vi.advanceTimersByTime(10_000)
    expect($spriteState.get()).toBe('idle')
  })
})

describe('companion-store disturbance tier', () => {
  beforeEach(() => {
    $userPreferredTier.set('normal')
    $effectiveTierOverride.set(null)
  })

  it('setDisturbanceTier updates user_preferred', () => {
    setDisturbanceTier('quiet')
    expect($userPreferredTier.get()).toBe('quiet')
  })

  it('effectiveTier follows user_preferred when no override', () => {
    setDisturbanceTier('proactive')
    expect($effectiveTier.get()).toBe('proactive')
  })

  it('effectiveTier follows override when set', () => {
    setDisturbanceTier('normal')
    $effectiveTierOverride.set('quiet')
    expect($effectiveTier.get()).toBe('quiet')
  })

  it('effectiveTier reverts to user_preferred when override cleared', () => {
    setDisturbanceTier('proactive')
    $effectiveTierOverride.set('quiet')
    $effectiveTierOverride.set(null)
    expect($effectiveTier.get()).toBe('proactive')
  })

  it('manual quiet is a hard lock-in (override ignored)', () => {
    // The plan says manual quiet is "locked in": even if the activity
    // monitor writes a stray override while the user has picked quiet,
    // the rendered effective tier stays quiet. The lock-in is enforced
    // in the computed atom itself so the rule cannot be regressed by a
    // monitor-side bug.
    setDisturbanceTier('quiet')
    $effectiveTierOverride.set('proactive')
    expect($effectiveTier.get()).toBe('quiet')
  })

  it('non-quiet user_preferred respects override', () => {
    // Inverse of the lock-in test: when user is NOT on quiet, the
    // activity monitor's override (e.g. immersive focus context) takes
    // effect.
    setDisturbanceTier('normal')
    $effectiveTierOverride.set('quiet')
    expect($effectiveTier.get()).toBe('quiet')

    setDisturbanceTier('proactive')
    $effectiveTierOverride.set('quiet')
    expect($effectiveTier.get()).toBe('quiet')
  })
})
