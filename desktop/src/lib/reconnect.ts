// Exponential backoff with jitter: 1s, 2s, 4s, 8s, 15s (capped). Jitter is
// proportional to base so attempt 0 lands in [1s, 1.5s] (not [1s, 8s]) — keeps
// the early-retry "fail-fast" property; final Math.min honors the cap.
const BASE_MS = 1_000
const MAX_DELAY_MS = 15_000
const MAX_SHIFT = 4
const JITTER_RATIO = 0.5

export function reconnectBackoffMs(attempt: number): number {
  const base = Math.min(MAX_DELAY_MS, BASE_MS * 2 ** Math.min(attempt, MAX_SHIFT))
  const jitter = Math.random() * base * JITTER_RATIO

  return Math.min(MAX_DELAY_MS, base + jitter)
}
