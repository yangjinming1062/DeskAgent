import type { ChatMediaItem } from '@/shared/types/spiritagent'

import { useResolvedMediaSrc } from './chat-media-src'
import { openMediaViewer } from './media-viewer-overlay'

// 聊天气泡内的媒体卡：轻量内联预览，点击打开全屏查看器。视频卡只做静帧预览，播放进查看器。
export function ChatMediaCard({ item }: { item: ChatMediaItem }): React.JSX.Element {
  const src = useResolvedMediaSrc(item)

  if (!src) {
    return (
      <div className="flex h-24 w-40 items-center justify-center rounded-lg border border-white/10 bg-white/5 text-xs text-white/40">
        {item.type === 'image' ? '🖼️ 加载中…' : '🎬 加载中…'}
      </div>
    )
  }

  return (
    <button
      className="block cursor-zoom-in overflow-hidden rounded-lg border border-white/10 bg-black/30 p-0 transition hover:border-white/30"
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
