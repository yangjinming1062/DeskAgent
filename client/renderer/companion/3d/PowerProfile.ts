import type { SpriteStateName } from '@/companion/companion-store'

// Render-power tiers for the always-resident companion window. The renderer
// process ships with Chromium throttling disabled (background chat streaming),
// so the 3D loop gates itself from these signals instead.

export type PowerProfile = 'active' | 'idle' | 'dormant'

export interface PowerSignals {
  spriteState: SpriteStateName
  screenLocked: boolean
  documentHidden: boolean
  fullscreen: boolean
  staticCovered: boolean
  modelSettled: boolean
}

// active and idle run at 60fps for smooth animations and fluid cursor look-at tracking.
// dormant is timer-driven at 4fps when locked/hidden/fullscreen/sleeping to minimize power.
export const PROFILE_FPS: Record<PowerProfile, number> = { active: 60, idle: 60, dormant: 4 }

export function resolvePowerProfile(signals: PowerSignals): PowerProfile {
  // Ready guard: until the first character model settles, hatching must run
  // at full rate — a dormant/idle boot would stretch GLB parse and texture
  // uploads across 250ms frames.
  if (!signals.modelSettled) {
    return 'active'
  }

  const dormant =
    signals.screenLocked ||
    signals.documentHidden ||
    signals.fullscreen ||
    signals.staticCovered ||
    signals.spriteState === 'sleeping'

  if (dormant) {
    return 'dormant'
  }

  return signals.spriteState === 'idle' || signals.spriteState === 'disconnected' ? 'idle' : 'active'
}
