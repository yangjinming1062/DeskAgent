import { $focusContext, $screenLocked } from '@/companion/activity'
import { $spriteState } from '@/companion/companion-store'
import { $activeSprite, $staticMode } from '@/companion/static-sprite/sprite-store'

import { $modelLoadSettled } from './model-store'
import type { PowerProfile } from './PowerProfile'
import { resolvePowerProfile } from './PowerProfile'
import type { PowerSignals } from './PowerProfile'

// Bridges the existing renderer atoms into power-profile changes for the 3D
// engine. Pure resolution lives in PowerProfile.ts; this module only owns the
// subscriptions so components stay free of signal plumbing.

function currentSignals(): PowerSignals {
  return {
    spriteState: $spriteState.get(),
    screenLocked: $screenLocked.get(),
    documentHidden: document.visibilityState !== 'visible',
    fullscreen: $focusContext.get()?.fullscreen ?? false,
    staticCovered: $staticMode.get() && !!$activeSprite.get(),
    modelSettled: $modelLoadSettled.get()
  }
}

export function subscribePowerProfile(apply: (profile: PowerProfile) => void): () => void {
  let last: PowerProfile | null = null

  const push = (): void => {
    const next = resolvePowerProfile(currentSignals())

    if (next !== last) {
      last = next
      apply(next)
    }
  }

  const unsubs = [
    $spriteState.listen(push),
    $screenLocked.listen(push),
    $focusContext.listen(push),
    $staticMode.listen(push),
    $activeSprite.listen(push),
    $modelLoadSettled.listen(push)
  ]

  const onVisibility = (): void => push()
  document.addEventListener('visibilitychange', onVisibility)

  push()

  return () => {
    for (const off of unsubs) {
      off()
    }

    document.removeEventListener('visibilitychange', onVisibility)
  }
}
