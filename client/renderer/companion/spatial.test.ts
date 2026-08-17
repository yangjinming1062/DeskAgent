import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $modelInfo } from './3d/model-store'
import { $companionLifecycle } from './companion-store'
import {
  $homePosition,
  $spatialLocale,
  $spatialLocomotion,
  $spatialPos,
  cancelMovement,
  getHomePosition,
  getSleepPosition,
  moveTo,
  setLocale,
  startRoam
} from './spatial'
import { $glbLoadFailed, $staticMode } from './static-sprite/sprite-store'

describe('spatial positioning & transitions', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    cancelMovement()
    $glbLoadFailed.set(false)
    $modelInfo.set({ ...$modelInfo.get(), asset_url: null, status: 'pending' })
    $companionLifecycle.set('ready')
    // Reset home position
    const home = getHomePosition()
    $homePosition.set(home)
    $spatialPos.set(home)
    $spatialLocale.set('home')
    $spatialLocomotion.set('still')
  })

  afterEach(() => {
    cancelMovement()
    vi.useRealTimers()
  })

  it('keeps sleep position at home/bottom-right location', () => {
    const home = $homePosition.get()
    const sleep = getSleepPosition()

    expect(sleep).toEqual(home)
  })

  it('teleports immediately without animation when in 2D static mode', () => {
    expect($staticMode.get()).toBe(true)

    const home = $homePosition.get()
    $spatialPos.set(home)

    const target = { x: home.x - 100, y: home.y - 100 }
    let arrived = false

    setLocale('sleep', {
      position: target,
      onArrive: () => {
        arrived = true
      }
    })

    expect($spatialPos.get()).toEqual(target)
    expect($spatialLocomotion.get()).toBe('still')
    expect(arrived).toBe(true)
  })

  it('moveTo teleports immediately in 2D static mode', () => {
    expect($staticMode.get()).toBe(true)

    const target = { x: 50, y: 50 }
    let arrived = false

    moveTo(target, 'walk', () => {
      arrived = true
    })

    expect($spatialPos.get()).toEqual(target)
    expect($spatialLocomotion.get()).toBe('still')
    expect(arrived).toBe(true)
  })

  it('prevents roaming in 2D static mode', () => {
    expect($staticMode.get()).toBe(true)

    startRoam()
    expect($spatialLocale.get()).not.toBe('roam')
  })

  it('interpolates movement smoothly when in 3D mode', () => {
    // Switch to 3D mode
    $modelInfo.set({ ...$modelInfo.get(), asset_url: 'http://model.glb', status: 'succeeded' })
    expect($staticMode.get()).toBe(false)

    const home = $homePosition.get()
    $spatialPos.set(home)
    const target = { x: home.x - 200, y: home.y }

    let arrived = false
    moveTo(target, 'walk', () => {
      arrived = true
    })

    expect($spatialLocomotion.get()).toBe('walk')
    expect(arrived).toBe(false)
  })
})
