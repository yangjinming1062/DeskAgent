import { OpfsBlobCache } from '@/shared/lib/opfs-blob-cache'
import { registerStorageClearHandler } from '@/shared/lib/storage'

const psdCache = new OpfsBlobCache({
  dirName: 'psd-cache',
  blobSuffix: '.psd',
  maxFiles: 10,
  maxBytes: 512 * 1024 * 1024,
  logTag: 'psd-opfs-cache'
})

export function clearPsdCache(): Promise<void> {
  return psdCache.clear()
}

export function deletePsdCache(contentHash: string): Promise<void> {
  return psdCache.delete(contentHash)
}

registerStorageClearHandler(clearPsdCache)

// PSD 魔术字节 '8BPS' = [0x38, 0x42, 0x50, 0x53]
function isValidPsdBuffer(buffer: ArrayBuffer): boolean {
  if (buffer.byteLength < 4) {
    return false
  }

  const u8 = new Uint8Array(buffer, 0, 4)

  return u8[0] === 0x38 && u8[1] === 0x42 && u8[2] === 0x50 && u8[3] === 0x53
}

/** PSD fetcher：apiAssetBuffer IPC（生产路径），puppet.html 调试页 fallback 到同源 fetch。
 *  abort 时返回 null，错误抛给上层走 throwOnError=true 原样抛给调用方（保留旧契约）。 */
 
async function psdFetcher(url: string, contentHash: string | null | undefined, signal: AbortSignal): Promise<ArrayBuffer | null> {
  if (typeof window.spiritagent?.apiAssetBuffer === 'function') {
    const u8 = await window.spiritagent.apiAssetBuffer({
      contentHash: contentHash || undefined,
      url
    })

    if (signal.aborted) {
      return null
    }

    return u8.slice().buffer
  }

  // eslint-disable-next-line no-restricted-syntax -- 同源 puppet.html 调试页，URL 来自同源 Vite asset，非后端相对路径
  const res = await fetch(url, { signal })

  if (!res.ok) {
    throw new Error(`psd fetch failed: ${res.status}`)
  }

  return await res.arrayBuffer()
}

/** 读取 PSD 字节：优先命中 OPFS 本地缓存；未命中时通过 IPC/网络拉取并异步回写本地缓存。
 *  throwOnError=true：abort / 拉取失败抛 DOMException / Error 给调用方（PuppetStage 的 try/catch）。 */
export async function fetchPsdWithCache(
  url: string,
  contentHash?: string | null,
  signal?: AbortSignal
): Promise<ArrayBuffer> {
  const buffer = await psdCache.fetchWithCache({
    contentHash,
    fetcher: sig => psdFetcher(url, contentHash, sig),
    signal,
    throwOnError: true,
    url,
    validate: isValidPsdBuffer
  })

  // throwOnError=true 下 fetchWithCache 不会返回 null；非 null 断言交给 TS。
  return buffer!
}
