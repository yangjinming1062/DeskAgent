// One-shot resolvers for the async avatar.regenerate flow: the RPC returns
// {queued, job_id} immediately and the result lands later as avatar.regenerated.

type Resolver = (payload: AvatarRegeneratedPayload) => void

const _pending = new Map<string, Resolver>()
// Buffers events that race ahead of the awaiter registration so a late-registering
// caller still settles instead of hanging. Cleared on resolve/timeout.
const _lateArrivals = new Map<string, AvatarRegeneratedPayload>()
// Tombstones for timed-out jobs so a late event is dropped, not re-buffered. Lazily pruned.
const _timedOut = new Map<string, number>()
const TOMBSTONE_TTL_MS = 10 * 60_000

// Above the 60s+ slow-provider image-gen ceiling so legitimate requests don't trip.
const REGEN_TIMEOUT_MS = 120_000

function _pruneTombstones() {
  const cutoff = Date.now() - TOMBSTONE_TTL_MS

  for (const [jobId, t] of _timedOut) {
    if (t < cutoff) {
      _timedOut.delete(jobId)
    }
  }
}

export interface AvatarRegeneratedPayload {
  job_id?: string
  asset_url?: string
  id?: number
  error?: string
}

export function awaitAvatarRegeneration(jobId: string): Promise<AvatarRegeneratedPayload> {
  return new Promise<AvatarRegeneratedPayload>((resolve, reject) => {
    const late = _lateArrivals.get(jobId)

    if (late) {
      _lateArrivals.delete(jobId)
      resolve(late)

      return
    }

    const timer = setTimeout(() => {
      if (_pending.get(jobId) === settle) {
        _pending.delete(jobId)
        // Tombstone so a subsequent late event is dropped, not re-buffered.
        _lateArrivals.delete(jobId)
        _timedOut.set(jobId, Date.now())
        reject(new Error(`avatar regeneration timed out for job ${jobId}`))
      }
    }, REGEN_TIMEOUT_MS)

    const settle: Resolver = payload => {
      clearTimeout(timer)
      resolve(payload)
    }

    _pending.set(jobId, settle)
  })
}

export function resolveAvatarRegeneration(payload: AvatarRegeneratedPayload): void {
  const jobId = payload.job_id

  if (!jobId) {
    return
  }

  _pruneTombstones()

  if (_timedOut.has(jobId)) {
    return
  }

  const cb = _pending.get(jobId)

  if (cb) {
    _pending.delete(jobId)
    cb(payload)

    return
  }

  _lateArrivals.set(jobId, payload)
}
