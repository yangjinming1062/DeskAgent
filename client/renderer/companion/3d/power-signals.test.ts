import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { $focusContext, $screenLocked } from '@/companion/activity'
import { $companionLifecycle, $spriteState } from '@/companion/companion-store'
import { $activeSprite, $glbLoadFailed, $staticMode } from '@/companion/static-sprite/sprite-store'

import { $modelInfo, $modelLoadSettled } from './model-store'
import { subscribePowerProfile } from './power-signals'

function setHidden(hidden: boolean): void {
  Object.defineProperty(document, 'visibilityState', {
    value: hidden ? 'hidden' : 'visible',
    configurable: true
  })
}

function setRenderableModel(renderable: boolean): void {
  $modelInfo.set({
    ...$modelInfo.get(),
    asset_url: renderable ? 'http://backend/glb' : null,
    status: renderable ? 'succeeded' : 'pending'
  })
}

describe('subscribePowerProfile', () => {
  let unsub: (() => void) | null = null

  beforeEach(() => {
    $spriteState.set('idle')
    $screenLocked.set(false)
    $focusContext.set(null)
    $activeSprite.set(null)
    $modelLoadSettled.set(true)
    $companionLifecycle.set('ready')
    $glbLoadFailed.set(false)
    setRenderableModel(true)
    setHidden(false)
  })

  afterEach(() => {
    unsub?.()
    unsub = null
  })

  it('emits active until the model settles, then follows the signals', () => {
    $modelLoadSettled.set(false)
    const profiles: string[] = []
    unsub = subscribePowerProfile(p => profiles.push(p))

    expect(profiles).toEqual(['active'])

    // 模型未稳定期间所有信号都被覆盖；一旦稳定，再切到 screenLocked 触发 dormant。
    $screenLocked.set(true)
    expect(profiles).toEqual(['active'])

    $modelLoadSettled.set(true)
    expect(profiles).toEqual(['active', 'dormant'])
  })

  it('dedupes unchanged resolutions across signal sources', () => {
    const profiles: string[] = []
    unsub = subscribePowerProfile(p => profiles.push(p))

    $spriteState.set('speaking')
    $spriteState.set('thinking')
    $activeSprite.set({ dataUrl: 'data:image/png;base64,AAA', tag: 't' })

    // speaking / thinking 都属于 active；仅在 $staticMode 为 false 时，单靠精灵图层不会覆盖 3D。
    expect(profiles).toEqual(['idle', 'active'])
  })

  it('follows static-mode coverage into dormant and back', () => {
    const profiles: string[] = []
    unsub = subscribePowerProfile(p => profiles.push(p))

    $activeSprite.set({ dataUrl: 'data:image/png;base64,AAA', tag: 't' })
    $glbLoadFailed.set(true)
    expect($staticMode.get()).toBe(true)
    expect(profiles).toEqual(['idle', 'dormant'])

    $glbLoadFailed.set(false)
    expect($staticMode.get()).toBe(false)
    expect(profiles).toEqual(['idle', 'dormant', 'idle'])
  })

  it('reacts to document visibility flips', () => {
    const profiles: string[] = []
    unsub = subscribePowerProfile(p => profiles.push(p))

    setHidden(true)
    document.dispatchEvent(new Event('visibilitychange'))
    expect(profiles).toEqual(['idle', 'dormant'])

    setHidden(false)
    document.dispatchEvent(new Event('visibilitychange'))
    expect(profiles).toEqual(['idle', 'dormant', 'idle'])
  })

  it('maps a fullscreen focus context to dormant', () => {
    const profiles: string[] = []
    unsub = subscribePowerProfile(p => profiles.push(p))

    $focusContext.set({ category: 'gaming', fullscreen: true })
    expect(profiles).toEqual(['idle', 'dormant'])
  })
})
