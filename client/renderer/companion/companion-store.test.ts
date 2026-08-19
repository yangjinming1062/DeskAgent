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

    // listening 优先级 40，working 优先级 70
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

    // 连续 6 次活动 tick 将 idle 翻转为 working（计数器阈值）。
    for (let i = 0; i < 6; i++) {
      reportUserActivity()
    }

    expect($spriteState.get()).toBe('working')

    // working（优先级 70）会盖住 idle（优先级 10）——不带 force 时，
    // 10 秒不活动计时器到期，状态仍会卡在 working。修复就是强制退出。
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
    // 计划规定手动安静是「锁定」的：即便活动监视器在用户已选安静时
    // 写入了一条意外的 override，渲染出的生效档位也保持安静。
    // 锁定规则在 computed atom 内部强制生效，因此不会被监视器侧的 bug 倒退。
    setDisturbanceTier('quiet')
    $effectiveTierOverride.set('proactive')
    expect($effectiveTier.get()).toBe('quiet')
  })

  it('non-quiet user_preferred respects override', () => {
    // 锁定测试的反向：当用户**未**选安静时，活动监视器的 override
    //（如沉浸式专注上下文）会生效。
    setDisturbanceTier('normal')
    $effectiveTierOverride.set('quiet')
    expect($effectiveTier.get()).toBe('quiet')

    setDisturbanceTier('proactive')
    $effectiveTierOverride.set('quiet')
    expect($effectiveTier.get()).toBe('quiet')
  })
})
