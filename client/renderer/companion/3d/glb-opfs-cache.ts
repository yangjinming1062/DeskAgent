import { log } from '@/shared/lib/log'
import { OpfsBlobCache } from '@/shared/lib/opfs-blob-cache'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'
import { $auth } from '@/shared/store/auth'

const glbCache = new OpfsBlobCache({
  dirName: 'glb-cache',
  blobSuffix: '.glb',
  maxFiles: 5,
  maxBytes: 512 * 1024 * 1024,
  logTag: 'glb-opfs-cache'
})

interface InFlightGlbFetch {
  controller: AbortController
  epoch: number
  promise: Promise<ArrayBuffer | null>
}

const inFlightFetches = new Map<string, InFlightGlbFetch>()

export function clearGlbCache(): Promise<void> {
  for (const item of inFlightFetches.values()) {
    item.controller.abort()
  }

  inFlightFetches.clear()

  return glbCache.clear()
}

registerStorageClearHandler(clearGlbCache)

// 键是 contentHash 而非 URL —— 后端的签名 URL 查询串会轮换。
export async function fetchGlbWithCache(
  url: string,
  contentHash?: string,
  signal?: AbortSignal
): Promise<ArrayBuffer | null> {
  if (signal?.aborted) {
    return null
  }

  // 登出 race：clearCompanionStorage 可能在本 fetch 启动后才推进 epoch。
  // 用快照 epoch 在 commit 前对照 currentClearEpoch()，把过期 fetch 拦下来。
  const fetchEpoch = currentClearEpoch()

  if (contentHash && currentClearEpoch() === fetchEpoch) {
    const cached = await glbCache.read(contentHash)

    if (currentClearEpoch() !== fetchEpoch) {
      return null
    }

    if (cached) {
      if (signal?.aborted) {
        return null
      }

      return cached
    }
  }

  if (signal?.aborted) {
    return null
  }

  const dedupeKey = contentHash || url
  let inFlight = inFlightFetches.get(dedupeKey)

  if (!inFlight) {
    const controller = new AbortController()

    const promise = (async () => {
      let bytes: ArrayBuffer | null = null

      try {
        if (typeof window.spiritagent?.apiAssetModelUrl === 'function') {
          const mediaUrl = await window.spiritagent.apiAssetModelUrl({
            url,
            contentHash: contentHash || undefined
          })

          if (controller.signal.aborted) {
            return null
          }

          // eslint-disable-next-line no-restricted-syntax -- URL 是主进程铸造的 spiritagent-media:// 自定义协议，非后端相对路径
          const res = await fetch(mediaUrl, { signal: controller.signal })

          if (!res.ok) {
            throw new Error(`Media protocol fetch failed with status ${res.status}`)
          }

          bytes = await res.arrayBuffer()
        } else {
          const u8 = await window.spiritagent.apiAssetBuffer({
            url,
            contentHash: contentHash || undefined
          })

          if (controller.signal.aborted) {
            return null
          }

          bytes = u8.slice().buffer
        }
      } catch (err) {
        if (!controller.signal.aborted) {
          log.warn('glb-opfs-cache', 'GLB fetch failed:', err)
        }

        return null
      } finally {
        inFlightFetches.delete(dedupeKey)
      }

      // 三道闸门：clearEpoch 未变 + authed + 未被 abort，避免过期 fetch 复活刚清空的 OPFS。
      if (
        contentHash &&
        bytes &&
        currentClearEpoch() === fetchEpoch &&
        $auth.get().kind === 'authenticated' &&
        !controller.signal.aborted
      ) {
        void glbCache.write(contentHash, bytes)
      }

      return bytes
    })()

    inFlight = { controller, epoch: fetchEpoch, promise }
    inFlightFetches.set(dedupeKey, inFlight)
  }

  const result = await inFlight.promise

  if (signal?.aborted) {
    return null
  }

  return result
}
