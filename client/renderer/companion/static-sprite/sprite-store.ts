import { atom, computed } from 'nanostores'

import { isClientErrorIpc } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'

import { $modelInfo } from '../3d/model-store'
import { $companionLifecycle } from '../companion-store'

// Static-sprite album store — the degraded renderer while no GLB is
// available (generating / failed / no key / load gap). The waiting image is
// requested once per static-mode entry; state & emotion changes map to
// free-form semantics (sprite-semantics.ts) and resolve via the backend's
// match-or-generate album. Failures keep the current image — never blank.

export interface ActiveSprite {
  dataUrl: string
  tag: string
}

export const $activeSprite = atom<ActiveSprite | null>(null)
// True when CharacterController.load fell through to the procedural egg —
// set from companion-3d's load effect; gates $hasRenderableModel until the
// GLB actually parses (model.ready flips 'succeeded' before the bytes land).
export const $glbLoadFailed = atom<boolean>(false)

interface SpriteResolveResponse {
  id: number
  url: string
  tag: string
  content_hash?: string | null
  generated: boolean
}

// Small PNGs over the apiAsset data-URL channel (same as wardrobe textures);
// keyed by content_hash so re-resolving a cached album row skips the fetch.
const _urlCache = new Map<string, string>()
const _resolvedRequests = new Set<string>()
const _inflight = new Map<string, Promise<void>>()

// Space distinct POSTs: the album is LLM-backed and state changes can burst
// (poke → interacting → previous state in quick succession). A dropped
// request re-fires on the next state change.
const MIN_REQUEST_SPACING_MS = 1500
let _lastPostAt = 0

/** Resolve a semantic request against the backend album; publishes to
 * $activeSprite on success and silently keeps the current image on failure. */
export async function requestSprite(request: string, role?: 'waiting'): Promise<void> {
  if (_resolvedRequests.has(request) || _inflight.has(request)) {
    return
  }

  if (Date.now() - _lastPostAt < MIN_REQUEST_SPACING_MS) {
    return
  }

  const task = (async () => {
    _lastPostAt = Date.now()

    try {
      const res = await window.spiritagent.api<SpriteResolveResponse>({
        path: '/api/companion/sprite',
        method: 'POST',
        body: role ? { request, role } : { request }
      })

      const cacheKey = res.content_hash ?? res.url
      let dataUrl = _urlCache.get(cacheKey)

      if (!dataUrl) {
        dataUrl = await window.spiritagent.apiAsset({ url: res.url })
        _urlCache.set(cacheKey, dataUrl)
      }

      _resolvedRequests.add(request)
      $activeSprite.set({ dataUrl, tag: res.tag })
    } catch (error) {
      if (!isClientErrorIpc(error)) {
        log.warn('sprite-store', 'requestSprite failed', error)
      }
    } finally {
      _inflight.delete(request)
    }
  })()

  _inflight.set(request, task)
  await task
}

/** Avatar regen invalidates the album's identity anchor (server filters by
 * avatar_id) — drop local caches so the next request generates fresh sprites. */
export function resetSpriteAlbum(): void {
  _urlCache.clear()
  _resolvedRequests.clear()
  _lastPostAt = 0
  $activeSprite.set(null)
}

export const $hasRenderableModel = computed(
  [$modelInfo, $glbLoadFailed],
  (model, loadFailed) => Boolean(model.asset_url) && model.status === 'succeeded' && !loadFailed
)

export const $staticMode = computed(
  [$companionLifecycle, $hasRenderableModel],
  (lifecycle, hasModel) => lifecycle !== 'unauthed' && !hasModel
)
