import { useEffect, useState } from 'react'

import type { ChatMediaItem } from '@/shared/types/spiritagent'

// 图片 data URL 进程内缓存：同一 URL 的媒体卡与查看器共享，避免重复 IPC 拉取。
const imageSrcCache = new Map<string, string>()

// 本地绝对路径（Windows 盘符 / UNC / POSIX 根）：这些 URL 不经过后端资产通道，
// 需要主进程直接读盘。后端媒体是 HTTP(S) URL 或相对路径，落不进这三个形态。
const LOCAL_PATH_RE = /^(?:[a-zA-Z]:[\\/]|\\\\|\/(?!\/))/

// apiAssetBuffer 只回字节不回 Content-Type，视频 blob 的 mime 由 URL 扩展名推导。
const VIDEO_MIME_BY_EXT: Record<string, string> = { '.mp4': 'video/mp4', '.mov': 'video/quicktime' }

/** 把媒体 URL 解析为渲染端可用 src：data URL 零开销直用；本地路径读盘；其余走后端资产通道。 */
function resolveImageSrc(url: string): string | Promise<string | null> {
  if (url.startsWith('data:')) {
    return url
  }

  if (LOCAL_PATH_RE.test(url)) {
    return window.spiritagent.readFileDataUrl(url).catch(() => null)
  }

  return window.spiritagent.apiAsset({ url }).catch(() => null)
}

/** 把后端媒体 URL 解析为渲染端可用 src：图片走 data URL 通道；视频取字节转 blob URL，组件卸载时回收。 */
export function useResolvedMediaSrc(item: ChatMediaItem): string | null {
  const [src, setSrc] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    let objectUrl: string | null = null

    void (async () => {
      try {
        if (item.type === 'image') {
          // data URL 直用时值即键，缓存是纯内存复制——只对走了 IPC 的形态缓存。
          const direct = item.url.startsWith('data:') ? item.url : null
          const cached = imageSrcCache.get(item.url)
          const fetched = direct || cached ? null : await resolveImageSrc(item.url)
          const dataUrl = direct || cached || fetched || null

          if (fetched) {
            imageSrcCache.set(item.url, fetched)
          }

          if (!cancelled) {
            setSrc(dataUrl)
          }
        } else {
          const buf = await window.spiritagent.apiAssetBuffer({ url: item.url })

          if (buf && !cancelled) {
            const clean = item.url.split(/[?#]/)[0]
            const ext = clean.slice(clean.lastIndexOf('.')).toLowerCase()
            // 拷贝进全新 ArrayBuffer——IPC 返回的 Uint8Array 类型上可能是 SharedArrayBuffer 视图，不满足 BlobPart。
            objectUrl = URL.createObjectURL(
              new Blob([new Uint8Array(buf)], { type: VIDEO_MIME_BY_EXT[ext] || 'video/mp4' })
            )
            setSrc(objectUrl)
          }
        }
      } catch {
        /* 解析失败保留占位态 */
      }
    })()

    return () => {
      cancelled = true

      if (objectUrl) {
        URL.revokeObjectURL(objectUrl)
      }
    }
  }, [item.type, item.url])

  return src
}
