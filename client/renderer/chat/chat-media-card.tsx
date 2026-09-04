import { openMediaViewer } from '@/companion'
import type { ChatMediaItem } from '@/shared/types/spiritagent'

import { useResolvedMediaSrc } from './chat-media-src'

// 聊天气泡内的媒体卡：轻量内联预览，点击打开全屏查看器。视频卡只做静帧预览，播放进查看器。
export function ChatMediaCard({ item }: { item: ChatMediaItem }): React.JSX.Element {
  const src = useResolvedMediaSrc(item)

  if (!src) {
    return (
      <div className="flex h-24 w-40 items-center justify-center rounded-lg border border-line-standard bg-fill-faint text-xs text-faint">
        {item.type === 'image' ? '图片加载中…' : '视频加载中…'}
      </div>
    )
  }

  return (
    <button
      className="block cursor-zoom-in overflow-hidden rounded-lg border border-line-standard bg-fill-trough p-0 transition hover:border-line-strong"
      onClick={() => openMediaViewer(item)}
      type="button"
    >
      {item.type === 'image' ? (
        <img alt="" className="block max-h-56 max-w-full object-contain" src={src} />
      ) : (
        <video className="block max-h-56 max-w-full" muted preload="metadata" src={src} />
      )}
    </button>
  )
}
