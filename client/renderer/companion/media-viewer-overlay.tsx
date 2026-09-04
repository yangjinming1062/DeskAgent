import { useStore } from '@nanostores/react'
import { atom } from 'nanostores'
import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'

import { useResolvedMediaSrc } from '@/chat'
import { useInteractiveRegion } from '@/companion/interactive-regions'
import type { ChatMediaItem } from '@/shared/types/spiritagent'

// 富媒体查看器：聊天窗媒体卡点击后全屏放大；图片查看与视频播放共用一个遮罩。
const $mediaViewer = atom<ChatMediaItem | null>(null)

export function openMediaViewer(item: ChatMediaItem): void {
  $mediaViewer.set(item)
}

export function MediaViewerOverlay(): React.ReactPortal | null {
  const item = useStore($mediaViewer)
  const overlayRef = useRef<HTMLDivElement>(null)

  // 打开时把整个视口注册为可交互区，避免点击穿透到下层窗口；函数引用稳定，effect 不会每次渲染重挂。
  const getViewportRect = (): DOMRect => new DOMRect(0, 0, window.innerWidth, window.innerHeight)

  useInteractiveRegion('media-viewer', overlayRef, getViewportRect)

  useEffect(() => {
    if (!item) {
      return
    }

    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') {
        $mediaViewer.set(null)
      }
    }

    window.addEventListener('keydown', onKey)

    return () => window.removeEventListener('keydown', onKey)
  }, [item])

  if (!item || typeof document === 'undefined') {
    return null
  }

  return createPortal(
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 p-6 backdrop-blur-sm"
      onClick={() => $mediaViewer.set(null)}
      ref={overlayRef}
      style={{ pointerEvents: 'auto' }}
    >
      <ViewerSurface item={item} />
    </div>,
    document.body
  )
}

function ViewerSurface({ item }: { item: ChatMediaItem }): React.JSX.Element {
  const src = useResolvedMediaSrc(item)

  return (
    <div className="flex max-h-full max-w-full items-center justify-center" onClick={e => e.stopPropagation()}>
      {item.type === 'image' ? (
        <img
          alt=""
          className="block max-h-[90vh] max-w-[90vw] rounded-2xl object-contain shadow-2xl"
          src={src ?? undefined}
        />
      ) : (
        <video
          autoPlay
          className="block max-h-[90vh] max-w-[90vw] rounded-2xl shadow-2xl"
          controls
          src={src ?? undefined}
        />
      )}
    </div>
  )
}
