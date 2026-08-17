import { describe, expect, it } from 'vitest'

import type { SpriteStateName } from '@/companion/companion-store'

import { type PowerSignals, PROFILE_FPS, resolvePowerProfile } from './PowerProfile'

const base: PowerSignals = {
  spriteState: 'idle',
  screenLocked: false,
  documentHidden: false,
  fullscreen: false,
  staticCovered: false,
  modelSettled: true
}

const withState = (spriteState: SpriteStateName): PowerSignals => ({ ...base, spriteState })

describe('resolvePowerProfile', () => {
  it('locks to active until the first model settles, overriding every dormant signal', () => {
    const boot: PowerSignals = {
      ...base,
      spriteState: 'sleeping',
      screenLocked: true,
      documentHidden: true,
      fullscreen: true,
      staticCovered: true,
      modelSettled: false
    }

    expect(resolvePowerProfile(boot)).toBe('active')
  })

  it.each([
    ['sleeping state', withState('sleeping')],
    ['locked screen', { ...base, screenLocked: true }],
    ['hidden document', { ...base, documentHidden: true }],
    ['fullscreen foreground app', { ...base, fullscreen: true }],
    ['static sprite covering the canvas', { ...base, staticCovered: true }]
  ])('maps %s to dormant', (_label, signals) => {
    expect(resolvePowerProfile(signals)).toBe('dormant')
  })

  it('keeps dormant even for otherwise-active states', () => {
    expect(resolvePowerProfile({ ...withState('speaking'), staticCovered: true })).toBe('dormant')
    expect(resolvePowerProfile({ ...withState('interacting'), screenLocked: true })).toBe('dormant')
  })

  it.each(['idle', 'disconnected'] as const)('maps %s to the idle tier', state => {
    expect(resolvePowerProfile(withState(state))).toBe('idle')
  })

  it.each(['speaking', 'thinking', 'listening', 'working', 'emotional', 'interacting'] as const)(
    'maps %s to active',
    state => {
      expect(resolvePowerProfile(withState(state))).toBe('active')
    }
  )

  it('orders frame budgets active >= idle > dormant', () => {
    expect(PROFILE_FPS.active).toBeGreaterThanOrEqual(PROFILE_FPS.idle)
    expect(PROFILE_FPS.idle).toBeGreaterThan(PROFILE_FPS.dormant)
  })
})
