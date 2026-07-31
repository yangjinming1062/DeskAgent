import { atom } from 'nanostores'

// Local environment signals polled from the Runner's system.* tools (plan §8).
// These bypass the LLM entirely — the companion reasons about them directly.
// When the Runner is offline the polls no-op and the atoms keep their defaults.

export const $screenLocked = atom<boolean>(false)

const POLL_INTERVAL_MS = 30_000

let timer: ReturnType<typeof setInterval> | null = null

async function pollOnce(): Promise<void> {
  const desktop = window.deskagent

  if (!desktop?.runnerInvoke) {return}

  try {
    const locked = await desktop.runnerInvoke('system.is_screen_locked', {})

    $screenLocked.set(Boolean((locked as { locked?: boolean } | null)?.locked))
  } catch {
    /* runner offline or tool unavailable — leave previous value */
  }
}

export function startActivityMonitor(): () => void {
  if (timer) {return () => stopActivityMonitor()}
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
