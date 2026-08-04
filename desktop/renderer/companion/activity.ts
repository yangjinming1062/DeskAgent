import { atom } from 'nanostores'

import { $gateway } from '@/shared/store/gateway'

// Local environment signals polled from the Runner's system.* tools (plan §8),
// bypassing the LLM — the companion reasons about them directly. Polls no-op
// while the Runner is offline and the atoms keep their defaults.

export const $screenLocked = atom<boolean>(false)

const POLL_INTERVAL_MS = 30_000

// Idle-triggered contextual affect (ARCHITECTURE.md §7.6). When the user has
// been inactive past IDLE_THRESHOLD_SECONDS and the cooldown window has
// elapsed, ping the backend's `companion.check_affect` RPC so the LLM can
// reason (persona + memory) whether the companion should express a contextual
// emotion. The desktop owns trigger timing; the backend owns emotion reasoning.
const IDLE_THRESHOLD_SECONDS = 30 * 60
const CHECK_COOLDOWN_MS = 60 * 60 * 1000

let timer: ReturnType<typeof setInterval> | null = null
let lastAffectCheckAt = 0

function maybeTriggerAffectCheck(idleSeconds: number, locked: boolean): void {
  if (locked || idleSeconds < IDLE_THRESHOLD_SECONDS) {
    return
  }

  const now = Date.now()

  if (now - lastAffectCheckAt < CHECK_COOLDOWN_MS) {
    return
  }

  const hour = new Date().getHours()

  // Quiet hours (23-7, synced with companion-store.checkBedtimeAndAutoSleep):
  // skip so an affect cue doesn't wake the companion past SLEEPING.
  if (hour >= 23 || hour < 7) {
    return
  }

  lastAffectCheckAt = now
  const gateway = $gateway.get()
  void gateway
    ?.request('companion.check_affect', {
      idle_seconds: idleSeconds,
      local_hour: hour
    })
    .catch(() => {
      /* backend offline or RPC failed — silent, next poll will retry after cooldown */
    })
}

async function pollOnce(): Promise<void> {
  const desktop = window.deskagent

  if (!desktop?.runnerInvoke) {
    return
  }

  try {
    const locked = await desktop.runnerInvoke('system.is_screen_locked', {})
    const isLocked = Boolean((locked as { locked?: boolean } | null)?.locked)
    $screenLocked.set(isLocked)

    let idleSeconds = 0

    try {
      const idle = await desktop.runnerInvoke('system.get_idle_seconds', {})
      idleSeconds = Number((idle as { idle_seconds?: number } | null)?.idle_seconds ?? 0)
    } catch {
      /* get_idle_seconds unavailable on this runner — skip affect check */
    }

    maybeTriggerAffectCheck(idleSeconds, isLocked)
  } catch {
    /* runner offline or is_screen_locked unavailable — leave previous values */
  }
}

export function startActivityMonitor(): () => void {
  if (timer) {
    return stopActivityMonitor
  }

  void pollOnce()
  timer = setInterval(() => void pollOnce(), POLL_INTERVAL_MS)

  return stopActivityMonitor
}

export function stopActivityMonitor(): void {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}
