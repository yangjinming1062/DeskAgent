import { atom } from 'nanostores'

// Companion lifecycle drives what the sprite window renders. Slice 1 only
// exercises unauthed-egg (pre-auth teaser) → ready (post-auth, gateway booted);
// onboarding/hatching arrive in Slice 2.
export type CompanionLifecycle = 'unauthed-egg' | 'onboarding' | 'hatching' | 'ready'

// MVP state-machine subset (plan.md §2 / §7): IDLE / THINKING / SPEAKING /
// WORKING, plus a DISCONNECTED overlay. EMOTIONAL/LISTENING/SLEEPING/INTERACTING
// arrive in later slices.
export type SpriteStateName = 'idle' | 'thinking' | 'speaking' | 'working' | 'disconnected'

export interface SpritePosition {
  x: number
  y: number
}

export const $companionLifecycle = atom<CompanionLifecycle>('unauthed-egg')
export const $spriteState = atom<SpriteStateName>('idle')
export const $spritePosition = atom<SpritePosition | null>(null)

export function setCompanionLifecycle(next: CompanionLifecycle): void {
  $companionLifecycle.set(next)
}

export function setSpriteState(name: SpriteStateName): void {
  $spriteState.set(name)
}

export function setSpritePosition(pos: SpritePosition | null): void {
  $spritePosition.set(pos)
}
