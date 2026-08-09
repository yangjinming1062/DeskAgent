// Late-arrival buffers for the two async portrait flows:
//
//   - ``avatar.regenerate``        → awaitAvatarRegeneration / resolveAvatarRegeneration
//   - ``avatar.generate_fullbody`` → awaitFullbodyGeneration / resolveFullbodyGeneration
//
// Both share the same timeout + tombstone machinery; payload shapes differ.
// Kept separate so a busted fullbody job doesn't trip the avatar awaiter.

type Resolver<T> = (payload: T) => void

interface PendingMap<T> {
  pending: Map<string, Resolver<T>>
  late: Map<string, T>
  timedOut: Map<string, number>
}

// Above the 60s+ slow-provider image-gen ceiling so legitimate requests don't trip.
const REGEN_TIMEOUT_MS = 120_000
const TOMBSTONE_TTL_MS = 10 * 60_000

function _pruneTombstones(map: Map<string, number>): void {
  const cutoff = Date.now() - TOMBSTONE_TTL_MS

  for (const [jobId, t] of map) {
    if (t < cutoff) {
      map.delete(jobId)
    }
  }
}

function _makeAwaiter<T>(
  store: PendingMap<T>,
  makeTombstoneError: (jobId: string) => Error
): (jobId: string) => Promise<T> {
  return (jobId: string): Promise<T> =>
    new Promise<T>((resolve, reject) => {
      const late = store.late.get(jobId)

      if (late) {
        store.late.delete(jobId)
        resolve(late)

        return
      }

      const timer = setTimeout(() => {
        if (store.pending.get(jobId) === settle) {
          store.pending.delete(jobId)
          store.late.delete(jobId)
          store.timedOut.set(jobId, Date.now())
          reject(makeTombstoneError(jobId))
        }
      }, REGEN_TIMEOUT_MS)

      const settle: Resolver<T> = payload => {
        clearTimeout(timer)
        resolve(payload)
      }

      store.pending.set(jobId, settle)
    })
}

function _makeResolver<T>(store: PendingMap<T>): (payload: T & { job_id?: string }) => void {
  return payload => {
    const jobId = payload.job_id

    if (!jobId) {
      return
    }

    _pruneTombstones(store.timedOut)

    if (store.timedOut.has(jobId)) {
      return
    }

    const cb = store.pending.get(jobId)

    if (cb) {
      store.pending.delete(jobId)
      cb(payload)

      return
    }

    store.late.set(jobId, payload)
  }
}

// Avatar (bust) regen.
export interface AvatarRegeneratedPayload {
  job_id?: string
  asset_url?: string | null
  // Step-1 only — the backend pushes null; older servers may still emit a URL.
  seed_url?: string | null
  id?: number
  error?: string
}

const _avatarStore: PendingMap<AvatarRegeneratedPayload> = {
  pending: new Map(),
  late: new Map(),
  timedOut: new Map()
}

export const awaitAvatarRegeneration = _makeAwaiter<AvatarRegeneratedPayload>(
  _avatarStore,
  jobId => new Error(`avatar regeneration timed out for job ${jobId}`)
)
export const resolveAvatarRegeneration = _makeResolver<AvatarRegeneratedPayload>(_avatarStore)

// Fullbody (seed) gen, kicked off after the avatar row is confirmed.
export interface FullbodyGeneratedPayload {
  job_id?: string
  seed_url?: string | null
  id?: number
  error?: string
}

const _fullbodyStore: PendingMap<FullbodyGeneratedPayload> = {
  pending: new Map(),
  late: new Map(),
  timedOut: new Map()
}

export const awaitFullbodyGeneration = _makeAwaiter<FullbodyGeneratedPayload>(
  _fullbodyStore,
  jobId => new Error(`avatar fullbody generation timed out for job ${jobId}`)
)
export const resolveFullbodyGeneration = _makeResolver<FullbodyGeneratedPayload>(_fullbodyStore)
