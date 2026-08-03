// One-shot promise resolvers for the async ``avatar.regenerate`` flow (P0-4).
// The RPC handler returns ``{queued: true, job_id}`` immediately and the
// real result lands later as an ``avatar.regenerated`` event. Callers
// ``awaitAvatarRegeneration(jobId)`` to get a single Promise that resolves
// with the event payload.

const _pending = new Map<string, (payload: AvatarRegeneratedPayload) => void>()

export interface AvatarRegeneratedPayload {
  job_id: string
  asset_url?: string
  id?: number
  error?: string
}

export function awaitAvatarRegeneration(jobId: string): Promise<AvatarRegeneratedPayload> {
  return new Promise<AvatarRegeneratedPayload>((resolve) => {
    _pending.set(jobId, resolve)
  })
}

export function resolveAvatarRegeneration(payload: AvatarRegeneratedPayload): void {
  const cb = _pending.get(payload.job_id)
  if (cb) {
    _pending.delete(payload.job_id)
    cb(payload)
  }
}
