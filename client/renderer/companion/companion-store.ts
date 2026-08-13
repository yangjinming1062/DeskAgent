import { atom, computed } from 'nanostores'

import { $llmAutonomy } from './prefs'

// Companion lifecycle drives what the sprite window renders. The renderer
// transitions unauthed → onboarding (during the wizard) → ready (after
// onboarding completes).
export type CompanionLifecycle = 'unauthed' | 'onboarding' | 'ready'

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

export type SpriteEmotion = string

export const BUILTIN_EMOTIONS: ReadonlySet<string> = new Set([
  'happy',
  'sad',
  'surprised',
  'excited',
  'confused',
  'concerned',
  'shy',
  'proud',
  'grateful',
  'playful',
  'bored',
  'lonely',
  'sleepy',
  'curious',
  'embarrassed',
  'apologetic'
])

export const $companionLifecycle = atom<CompanionLifecycle>('unauthed')
export const $spriteState = atom<SpriteStateName>('idle')
// True during a live voice-call; read by useGatewayBoot to defer the
// disconnected→sleeping escalation so a gateway flap doesn't clobber an active call.
export const $voiceCallOpen = atom<boolean>(false)
export const $spriteEmotion = atom<SpriteEmotion | null>(null)
export const $previousState = atom<SpriteStateName>('idle')
export const $clipOverride = atom<string | null>(null)

// Disturbance tier gates the companion's proactive behaviour (ARCHITECTURE.md §6 /
// plan.md §4.2). User-initiated actions are never gated — only proactive
// outbound (companion.message). `quiet` blocks proactive messages but keeps
// the affect channel open (phase 2).
//
// Two-atoms model:
// - ``$userPreferredTier`` — the user's manual choice in the settings UI,
//   persisted to localStorage, source of truth. The activity monitor reads
//   this when deciding whether to override.
// - ``$effectiveTierOverride`` — set by the activity monitor when the user
//   is in an immersive / fullscreen focus context. ``null`` means "no
//   override; effective = user preferred".
// - ``$effectiveTier`` — derived from the two above. This is what the
//   rest of the renderer reads to decide whether to gate proactive
//   channels; the settings-overlay / chat-dock pills still display the
//   user_preferred value so the chip reflects the user's actual choice
//   rather than a transient override.
export type DisturbanceTier = 'proactive' | 'normal' | 'quiet'

// Persist the chosen tier in localStorage so a Desktop restart
// doesn't silently reset the user to the (more chatty) default. The
// backend has its own process-local cache (services/companion/disturbance.py)
// but the desktop is the source of truth — the desktop reports the tier
// back to the backend on every change AND on gateway open.
const _storedTier = (typeof localStorage !== 'undefined' &&
  localStorage.getItem('da.companion.disturbanceTier')) as DisturbanceTier | null

const _validStored: DisturbanceTier | null =
  _storedTier === 'proactive' || _storedTier === 'normal' || _storedTier === 'quiet' ? _storedTier : null

export const $userPreferredTier = atom<DisturbanceTier>(_validStored ?? 'normal')
// ``null`` means "no override active; effective falls back to user_preferred".
// The activity monitor (activity.ts) is the only writer.
export const $effectiveTierOverride = atom<DisturbanceTier | null>(null)

// Manual quiet is a hard lock-in: even if the activity monitor writes an
// override while the user has manually chosen quiet, the rendered effective
// tier stays quiet. Other overrides (proactive / normal) only apply when the
// user has not picked quiet.
export const $effectiveTier = computed([$userPreferredTier, $effectiveTierOverride], (preferred, override) =>
  preferred === 'quiet' ? 'quiet' : (override ?? preferred)
)

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

// States that auto-revert via ``$previousState`` + the timer below. They
// bypass the priority gate so an in-flight WORKING/SPEAKING animation
// doesn't suppress a transient emotion/interaction cue — adding a new
// transient state is one entry here, not three code sites.
const TRANSIENT_STATES: ReadonlySet<SpriteStateName> = new Set(['emotional', 'interacting'])

let transientTimer: ReturnType<typeof setTimeout> | null = null

export function setCompanionLifecycle(next: CompanionLifecycle): void {
  $companionLifecycle.set(next)
}

export function setSpriteState(
  name: SpriteStateName,
  options?: { emotion?: SpriteEmotion; durationMs?: number; force?: boolean }
): void {
  const current = $spriteState.get()

  if (
    !options?.force &&
    STATE_PRIORITY[name] < STATE_PRIORITY[current] &&
    current !== 'idle' &&
    !TRANSIENT_STATES.has(name)
  ) {
    // Lower priority state cannot interrupt higher priority state —
    // except transient states, which auto-revert via the timer below.
    return
  }

  if (options?.force) {
    if (transientTimer) {
      clearTimeout(transientTimer)
      transientTimer = null
    }
  }

  if (TRANSIENT_STATES.has(name)) {
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

      const target =
        currentAfter !== 'emotional' && currentAfter !== 'interacting'
          ? currentAfter
          : storedPrev === 'emotional' || storedPrev === 'interacting'
            ? 'idle'
            : storedPrev

      $spriteState.set(target)
    }, ms)

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
  if ($llmAutonomy.get()) {
    return false
  }

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

  if (current !== 'idle' && current !== 'working') {
    return
  }

  activityCounter += 1

  if (activityCounter >= 6 && current === 'idle') {
    setSpriteState('working')
  }

  if (activityResetTimer) {
    clearTimeout(activityResetTimer)
  }

  activityResetTimer = setTimeout(() => {
    activityCounter = 0

    if ($spriteState.get() === 'working') {
      // ``working`` (pri 70) gates ``idle`` (pri 10) — without ``force: true``
      // the timer expires but the state stays locked on the working badge.
      // Explicitly force the exit so the sprite returns to idle once
      // the user stops producing activity for the configured window.
      setSpriteState('idle', { force: true })
    }
  }, 10000)
}

export function setDisturbanceTier(tier: DisturbanceTier): void {
  $userPreferredTier.set(tier)

  if (typeof localStorage !== 'undefined') {
    try {
      localStorage.setItem('da.companion.disturbanceTier', tier)
    } catch {
      // localStorage may be disabled (private mode); the in-memory atom
      // is the only thing the rest of the code reads, so silently keep going.
    }
  }
}
