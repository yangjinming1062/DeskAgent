import { atom } from 'nanostores'

// Companion lifecycle drives what the sprite window renders. Slice 1 only
// exercises unauthed-egg (pre-auth teaser) → ready (post-auth, gateway booted);
// onboarding/hatching arrive in Slice 2.
export type CompanionLifecycle = 'unauthed-egg' | 'onboarding' | 'hatching' | 'ready'

// Phase 2 state-machine (plan.md §2):
// IDLE / LISTENING / THINKING / SPEAKING / WORKING / EMOTIONAL / SLEEPING / INTERACTING / DISCONNECTED
export type SpriteStateName =
  | 'idle'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'working'
  | 'emotional'
  | 'sleeping'
  | 'interacting'
  | 'disconnected'

export type SpriteEmotion =
  | 'happy'
  | 'sad'
  | 'surprised'
  | 'excited'
  | 'confused'
  | 'concerned'
  | 'shy'
  | 'proud'
  | 'grateful'
  | 'playful'
  | 'bored'
  | 'lonely'

export interface SpritePosition {
  x: number
  y: number
}

export const $companionLifecycle = atom<CompanionLifecycle>('unauthed-egg')
export const $spriteState = atom<SpriteStateName>('idle')
export const $spriteEmotion = atom<SpriteEmotion | null>(null)
export const $previousState = atom<SpriteStateName>('idle')
export const $spritePosition = atom<SpritePosition | null>(null)

// Disturbance tier gates the companion's proactive behaviour (ARCHITECTURE.md §6 /
// plan.md §4.2). User-initiated actions are never gated — only proactive
// outbound (companion.message). `quiet` blocks proactive messages but keeps
// the affect channel open (phase 2).
export type DisturbanceTier = 'proactive' | 'normal' | 'quiet'

export const $disturbanceTier = atom<DisturbanceTier>('normal')

const STATE_PRIORITY: Record<SpriteStateName, number> = {
  disconnected: 100,
  interacting: 80,
  working: 70,
  speaking: 60,
  thinking: 50,
  listening: 40,
  emotional: 35,
  sleeping: 30,
  idle: 10
}

let transientTimer: ReturnType<typeof setTimeout> | null = null

export function setCompanionLifecycle(next: CompanionLifecycle): void {
  $companionLifecycle.set(next)
}

export function setSpriteState(
  name: SpriteStateName,
  options?: { emotion?: SpriteEmotion; durationMs?: number; force?: boolean }
): void {
  const current = $spriteState.get()

  if (options?.force) {
    if (transientTimer) {
      clearTimeout(transientTimer)
      transientTimer = null
    }
  }

  if (name === 'emotional' || name === 'interacting') {
    if (current !== 'emotional' && current !== 'interacting') {
      $previousState.set(current)
    }
    if (options?.emotion) {
      $spriteEmotion.set(options.emotion)
    }

    $spriteState.set(name)

    if (transientTimer) {
      clearTimeout(transientTimer)
    }

    const ms = options?.durationMs ?? (name === 'emotional' ? 2500 : 1800)
    transientTimer = setTimeout(() => {
      transientTimer = null
      $spriteEmotion.set(null)
      const prev = $previousState.get()
      $spriteState.set(prev === 'emotional' || prev === 'interacting' ? 'idle' : prev)
    }, ms)
    return
  }

  if (!options?.force && STATE_PRIORITY[name] < STATE_PRIORITY[current] && current !== 'idle') {
    // Lower priority state cannot interrupt higher priority state
    return
  }

  if (transientTimer) {
    clearTimeout(transientTimer)
    transientTimer = null
  }

  $spriteEmotion.set(options?.emotion ?? null)
  $spriteState.set(name)
}

export function setSpritePosition(pos: SpritePosition | null): void {
  $spritePosition.set(pos)
}

export function setDisturbanceTier(tier: DisturbanceTier): void {
  $disturbanceTier.set(tier)
}

