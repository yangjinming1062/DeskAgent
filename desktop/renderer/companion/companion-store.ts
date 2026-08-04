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
  // P1-5: align with the expanded ``ALLOWED_EMOTIONS`` in
  // ``services/chat/affect.py`` so the LLM's vocabulary has a typed
  // landing pad on the desktop. ``neutral`` deliberately NOT in this
  // union — it's filtered at the events.ts boundary (P0-2 + P1-5) and
  // maps to a plain ``idle`` return.
  | 'sleepy'
  | 'curious'
  | 'embarrassed'
  | 'apologetic'

export interface SpritePosition {
  x: number
  y: number
}

export const $companionLifecycle = atom<CompanionLifecycle>('unauthed-egg')
export const $spriteState = atom<SpriteStateName>('idle')
// True during a live voice-call; read by useGatewayBoot to defer the
// disconnected→sleeping escalation so a gateway flap doesn't clobber an active call.
export const $voiceCallOpen = atom<boolean>(false)
export const $spriteEmotion = atom<SpriteEmotion | null>(null)
export const $previousState = atom<SpriteStateName>('idle')
export const $spritePosition = atom<SpritePosition | null>(null)

// Disturbance tier gates the companion's proactive behaviour (ARCHITECTURE.md §6 /
// plan.md §4.2). User-initiated actions are never gated — only proactive
// outbound (companion.message). `quiet` blocks proactive messages but keeps
// the affect channel open (phase 2).
export type DisturbanceTier = 'proactive' | 'normal' | 'quiet'

// P1-18: persist the chosen tier in localStorage so a Desktop restart
// doesn't silently reset the user to the (more chatty) default. The
// backend has its own process-local cache (services/companion/disturbance.py)
// but the desktop is the source of truth — the desktop reports the tier
// back to the backend on every change AND on gateway open.
const _storedTier = (typeof localStorage !== 'undefined' && localStorage.getItem('da.companion.disturbanceTier')) as DisturbanceTier | null
const _validStored: DisturbanceTier | null = _storedTier === 'proactive' || _storedTier === 'normal' || _storedTier === 'quiet' ? _storedTier : null
export const $disturbanceTier = atom<DisturbanceTier>(_validStored ?? 'normal')

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
      // Prefer the current state if a higher-priority one arrived mid-transient.
      const currentAfter = $spriteState.get()
      const storedPrev = $previousState.get()
      const target = currentAfter !== 'emotional' && currentAfter !== 'interacting' ? currentAfter : (storedPrev === 'emotional' || storedPrev === 'interacting' ? 'idle' : storedPrev)
      $spriteState.set(target)
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

export function checkBedtimeAndAutoSleep(): boolean {
  const hour = new Date().getHours()
  const isNight = hour >= 23 || hour < 7

  if (isNight && $spriteState.get() === 'idle') {
    setSpriteState('sleeping')

    return true
  }

  return false
}

export function wakeUpFromSleep(): void {
  if ($spriteState.get() === 'sleeping') {
    setSpriteState('idle', { force: true })
  }
}

let activityCounter = 0
let activityResetTimer: ReturnType<typeof setTimeout> | null = null

export function reportUserActivity(): void {
  const current = $spriteState.get()

  if (current !== 'idle' && current !== 'working') {return}

  activityCounter += 1

  if (activityCounter >= 6 && current === 'idle') {
    setSpriteState('working')
  }

  if (activityResetTimer) {clearTimeout(activityResetTimer)}
  activityResetTimer = setTimeout(() => {
    activityCounter = 0

    if ($spriteState.get() === 'working') {
      // ``working`` (pri 70) gates ``idle`` (pri 10) — without ``force: true``
      // the timer expires but the state stays locked on the working badge.
      // P0-9: explicitly force the exit so the sprite returns to idle once
      // the user stops producing activity for the configured window.
      setSpriteState('idle', { force: true })
    }
  }, 10000)
}

export function setSpritePosition(pos: SpritePosition | null): void {
  $spritePosition.set(pos)
}

export function setDisturbanceTier(tier: DisturbanceTier): void {
  $disturbanceTier.set(tier)
  if (typeof localStorage !== 'undefined') {
    try {
      localStorage.setItem('da.companion.disturbanceTier', tier)
    } catch {
      // localStorage may be disabled (private mode); the in-memory atom
      // is the only thing the rest of the code reads, so silently keep going.
    }
  }
}

