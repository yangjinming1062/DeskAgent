import { log } from '@/shared/lib/log'
import { OpfsBlobCache } from '@/shared/lib/opfs-blob-cache'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'
import { $auth } from '@/shared/store/auth'

const psdCache = new OpfsBlobCache({
  dirName: 'psd-cache',
  blobSuffix: '.psd',
  maxFiles: 10,
  maxBytes: 512 * 1024 * 1024,
  logTag: 'psd-opfs-cache'
})

interface InFlightPsdFetch {
  controller: AbortController
  epoch: number
  promise: Promise<ArrayBuffer>
}

const inFlightFetches = new Map<string, InFlightPsdFetch>()

export function clearPsdCache(): Promise<void> {
  for (const item of inFlightFetches.values()) {
    item.controller.abort()
  }

  inFlightFetches.clear()

  return psdCache.clear()
}

export function deletePsdCache(contentHash: string): Promise<void> {
  return psdCache.delete(contentHash)
}

registerStorageClearHandler(clearPsdCache)

function isValidPsdBuffer(buffer: ArrayBuffer): boolean {
  if (buffer.byteLength < 4) {
    return false
  }

  const u8 = new Uint8Array(buffer, 0, 4)

  // PSD 魔术字节 '8BPS' = [0x38, 0x42, 0x50, 0x53]
  return u8[0] === 0x38 && u8[1] === 0x42 && u8[2] === 0x50 && u8[3] === 0x53
}

/** 读取 PSD 字节：优先命中 OPFS 本地缓存；未命中时通过 IPC/网络拉取并异步回写本地缓存。
 * 支持请求去重、独立 AbortSignal 检查与魔术字节校验。 */
export async function fetchPsdWithCache(
  url: string,
  contentHash?: string | null,
  signal?: AbortSignal
): Promise<ArrayBuffer> {
  if (signal?.aborted) {
    throw new DOMException('Aborted', 'AbortError')
  }

  // 读侧也按 epoch 短路：登出 race 里 clearCompanionStorage 可能在本 IIFE 启动
  // 后才推进 epoch，避免继续读刚被清空的 OPFS。
  const fetchEpoch = currentClearEpoch()

  if (contentHash && currentClearEpoch() === fetchEpoch) {
    const cached = await psdCache.read(contentHash)

    if (currentClearEpoch() !== fetchEpoch) {
      throw new DOMException('Aborted', 'AbortError')
    }

    if (cached) {
      if (isValidPsdBuffer(cached)) {
        log.info('psd-opfs-cache', 'OPFS hit:', contentHash)

        if (signal?.aborted) {
          throw new DOMException('Aborted', 'AbortError')
        }

        return cached
      }

      void deletePsdCache(contentHash)
    }
  }

  if (signal?.aborted) {
    throw new DOMException('Aborted', 'AbortError')
  }

  const dedupeKey = contentHash || url
  let inFlight = inFlightFetches.get(dedupeKey)

  if (!inFlight) {
    const controller = new AbortController()

    const promise = (async () => {
      try {
        let buffer: ArrayBuffer

        if (typeof window.spiritagent?.apiAssetBuffer === 'function') {
          const u8 = await window.spiritagent.apiAssetBuffer({ contentHash: contentHash || undefined, url })

          if (controller.signal.aborted) {
            throw new DOMException('Aborted', 'AbortError')
          }

          buffer = u8.slice().buffer
        } else {
          // eslint-disable-next-line no-restricted-syntax -- 同源 puppet.html 调试页，URL 来自同源 Vite asset，非后端相对路径
          const res = await fetch(url, { signal: controller.signal })

          if (!res.ok) {
            throw new Error(`psd fetch failed: ${res.status}`)
          }

          buffer = await res.arrayBuffer()
        }

        if (!isValidPsdBuffer(buffer)) {
          throw new Error('Downloaded asset is not a valid PSD (invalid magic bytes)')
        }

        // 三道闸门：clearEpoch 没变（避免写进刚清空的 OPFS）+ authed + 未被 abort
        if (
          contentHash &&
          buffer &&
          currentClearEpoch() === fetchEpoch &&
          $auth.get().kind === 'authenticated' &&
          !controller.signal.aborted
        ) {
          void psdCache.write(contentHash, buffer)
        }

        return buffer
      } finally {
        inFlightFetches.delete(dedupeKey)
      }
    })()

    inFlight = { controller, epoch: fetchEpoch, promise }
    inFlightFetches.set(dedupeKey, inFlight)
  }

  const result = await inFlight.promise

  if (signal?.aborted) {
    throw new DOMException('Aborted', 'AbortError')
  }

  return result
}
