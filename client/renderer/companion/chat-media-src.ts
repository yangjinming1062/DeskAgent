import { useEffect, useState } from 'react'

import type { ChatMediaItem } from '@/shared/types/spiritagent'

// 图片 data URL 进程内缓存：同一 URL 的媒体卡与查看器共享，避免重复 IPC 拉取。
const imageSrcCache = new Map<string, string>()

/** 把后端媒体 URL 解析为渲染端可用 src：图片走 data URL 通道；视频取字节转 blob URL，组件卸载时回收。 */
export function useResolvedMediaSrc(item: ChatMediaItem): string | null {
  const [src, setSrc] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    let objectUrl: string | null = null

    void (async () => {
      try {
        if (item.type === 'image') {
          const cached = imageSrcCache.get(item.url)
          const dataUrl = cached || (await window.spiritagent.apiAsset({ url: item.url })) || null

          if (dataUrl) {
            imageSrcCache.set(item.url, dataUrl)
          }

          if (!cancelled) {
            setSrc(dataUrl)
          }
        } else {
          const buf = await window.spiritagent.apiAssetBuffer({ url: item.url })

          if (buf && !cancelled) {
            // 拷贝进全新 ArrayBuffer——IPC 返回的 Uint8Array 类型上可能是 SharedArrayBuffer 视图，不满足 BlobPart。
            objectUrl = URL.createObjectURL(new Blob([new Uint8Array(buf)], { type: 'video/mp4' }))
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
