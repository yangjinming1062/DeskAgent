import { atom } from 'nanostores'

import { $spriteEmotion } from '@/companion/companion-store'
import { isClientErrorIpc } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'

// Chat-dock expression avatars — the emotion face shown beside the chat while
// an affect is active. Lookup is tag-keyed (the backend resolves
// match-or-generate by emotion token); failures keep the portrait fallback.

export interface ExpressionAvatar {
  name: string
  dataUrl: string
}

export const $expressionAvatar = atom<ExpressionAvatar | null>(null)

// Resolved per emotion token (the request key is 1:1 with the server row, so
// a single cache suffices — unlike the sprite album, where free-form requests
// can LLM-match the same image). Small PNGs travel over the apiAsset
// data-URL channel, same as wardrobe textures.
const _resolvedCache = new Map<string, ExpressionAvatar>()
const _inflight = new Map<string, Promise<void>>()
const _failedAt = new Map<string, number>()

const _FAILURE_BACKOFF_MS = 60_000
// Bumped by resetExpressionAvatars — a task that started before the reset
// (avatar regenerated mid-generation) must not re-cache its stale result.
let _resetEpoch = 0

/** Resolve the emotion's avatar; publishes to $expressionAvatar while the
 * emotion is still active. neutral / no-op emotions never request. */
export async function requestExpressionAvatar(name: string): Promise<void> {
  const normalized = name.trim().toLowerCase()

  if (!normalized || normalized === 'neutral') {
    return
  }

  const cached = _resolvedCache.get(normalized)

  if (cached) {
    $expressionAvatar.set(cached)

    return
  }

  const failedAt = _failedAt.get(normalized)

  if (failedAt !== undefined && Date.now() - failedAt < _FAILURE_BACKOFF_MS) {
    return
  }

  const inflight = _inflight.get(normalized)

  if (inflight) {
    // The owning task publishes / caches / backs off on completion — the
    // joiner only waits; re-running that logic here would duplicate the guard.
    await inflight

    return
  }

  const epoch = _resetEpoch

  const task = (async () => {
    try {
      const res = await window.spiritagent.api<{ url: string }>({
        path: '/api/companion/expression-avatar',
        method: 'POST',
        body: { name: normalized }
      })

      const dataUrl = await window.spiritagent.apiAsset({ url: res.url })
      const active: ExpressionAvatar = { name: normalized, dataUrl }

      // A slow generation is never wasted: the result always lands in the
      // caches (server row + here) and shows instantly on the next use. The
      // display swaps in only while this emotion is still the active one.
      if (epoch === _resetEpoch) {
        _resolvedCache.set(normalized, active)

        if ($spriteEmotion.get() === normalized) {
          $expressionAvatar.set(active)
        }
      }
    } catch (error) {
      if (!isClientErrorIpc(error)) {
        log.warn('expression-avatar', 'requestExpressionAvatar failed', error)
      }

      // Portrait fallback for the active emotion — even if the display still
      // holds a stale previous face.
      if (epoch === _resetEpoch) {
        _failedAt.set(normalized, Date.now())

        if ($spriteEmotion.get() === normalized) {
          $expressionAvatar.set(null)
        }
      }
    } finally {
      _inflight.delete(normalized)
    }
  })()

  _inflight.set(normalized, task)
  await task
}

/** Drop the display only (emotion ended) — caches survive for the next use. */
export function clearExpressionAvatar(): void {
  $expressionAvatar.set(null)
}

/** Avatar regen invalidates the identity anchor (server keys rows by
 * avatar_id) — drop local caches so the next request resolves fresh images. */
export function resetExpressionAvatars(): void {
  _resetEpoch++
  _resolvedCache.clear()
  _inflight.clear()
  _failedAt.clear()
  $expressionAvatar.set(null)
}
