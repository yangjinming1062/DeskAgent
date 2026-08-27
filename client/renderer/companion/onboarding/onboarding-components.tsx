import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import { useInteractiveRegion } from '@/companion/interactive-regions'
import { useLatestRef } from '@/shared/hooks/use-latest-ref'
import { CHIP_FILTER, CHIP_FILTER_ACTIVE } from '@/shared/panel'

// 从 onboarding-flow.tsx 中抽离出的四个小 JSX 组件。
// 所有输入都通过 props 传入——不依赖模块级共享状态——便于单独隔离测试。

export function Chip({
  label,
  onClick,
  active
}: {
  label: string
  onClick: () => void
  active?: boolean
}): React.JSX.Element {
  return (
    <button className={active ? CHIP_FILTER_ACTIVE : CHIP_FILTER} onClick={onClick} type="button">
      {label}
    </button>
  )
}

export interface HistoryGalleryItem {
  url: string | null
}

export function PortraitPanel({
  avatarUrl,
  name,
  hint,
  introHint,
  history,
  selectedIdx,
  onSelectEntry
}: {
  avatarUrl: string | null
  name: string
  hint: string | null
  introHint?: string | null
  history?: HistoryGalleryItem[]
  selectedIdx?: number
  onSelectEntry?: (idx: number) => void
}): React.JSX.Element {
  const [zoomedUrl, setZoomedUrl] = useState<string | null>(null)

  const gallery =
    history && history.length > 1 && onSelectEntry ? (
      <HistoryGallery entries={history} onSelect={onSelectEntry} selectedIdx={selectedIdx ?? history.length - 1} />
    ) : null

  return (
    <div className="flex flex-col items-center gap-2">
      {introHint && <p className="text-center text-[10px] leading-relaxed text-white/45">{introHint}</p>}
      <PortraitThumb
        label="头像"
        name={name}
        onZoom={avatarUrl ? () => setZoomedUrl(avatarUrl) : undefined}
        size="lg"
        url={avatarUrl}
      />
      {gallery}
      {hint && <p className="text-xs text-rose-300/90">{hint}</p>}
      {zoomedUrl && <PortraitLightbox name={name} onClose={() => setZoomedUrl(null)} url={zoomedUrl} />}
    </div>
  )
}

export function HistoryGallery({
  entries,
  selectedIdx,
  onSelect
}: {
  entries: HistoryGalleryItem[]
  selectedIdx: number
  onSelect: (idx: number) => void
}): React.JSX.Element {
  return (
    <div className="mt-1 flex justify-center gap-1.5">
      {entries.map((entry, idx) => {
        return (
          <button
            className={`overflow-hidden rounded-md border transition ${
              idx === selectedIdx ? 'border-[#6c8aff]' : 'border-white/15 opacity-60 hover:opacity-90'
            }`}
            key={idx}
            onClick={() => onSelect(idx)}
            type="button"
          >
            {entry.url ? (
              <img alt="" className="h-10 w-10 object-cover" src={entry.url} />
            ) : (
              <div className="grid h-10 w-10 place-items-center text-[10px] text-white/30">—</div>
            )}
          </button>
        )
      })}
    </div>
  )
}

// 立绘画幅随物种骨骼分桶（竖/方/横并存），取景框比例须跟随图片本身——
// 这里探测 naturalWidth/Height 供容器设 aspectRatio；加载完成前返回 null（调用方回退双足竖版占位）。
export function useNaturalAspectRatio(src: string | null | undefined): number | null {
  const [ratio, setRatio] = useState<number | null>(null)

  useEffect(() => {
    setRatio(null)

    if (!src) {
      return
    }

    let alive = true
    const img = new Image()

    img.onload = () => {
      if (alive && img.naturalWidth > 0 && img.naturalHeight > 0) {
        setRatio(img.naturalWidth / img.naturalHeight)
      }
    }

    img.src = src

    return () => {
      alive = false
    }
  }, [src])

  return ratio
}

function PortraitThumb({
  label,
  name,
  onZoom,
  url,
  size = 'sm'
}: {
  label: string
  name: string
  onZoom: (() => void) | undefined
  url: string | null
  size?: 'sm' | 'md' | 'lg'
}): React.JSX.Element {
  const sizeClass = size === 'lg' ? 'h-48 w-48' : size === 'sm' ? 'h-28 w-28' : 'h-36 w-36'

  return (
    <div className="flex flex-col items-center gap-1">
      {url ? (
        <div className="group relative">
          <button
            aria-label="放大查看"
            className="block cursor-zoom-in overflow-hidden rounded-xl border-0 bg-transparent p-0"
            onClick={onZoom}
            type="button"
          >
            <img alt={name} className={`${sizeClass} object-cover shadow-lg`} src={url} />
          </button>
        </div>
      ) : (
        <div
          className={`grid ${sizeClass} place-items-center rounded-xl bg-white/5 text-center text-[10px] text-white/30`}
        >
          —
        </div>
      )}
      <span className="text-[10px] text-white/40">{label}</span>
    </div>
  )
}

// 灯箱按立绘本身尺寸渲染——不铺满整屏深色遮罩。图片本身即窗口，关闭按钮叠在其右上角；
// 半透明背景负责捕获外部点击。通过 createPortal 挂到 document.body，
// 避免 onboarding 容器的 `backdrop-filter`（依 CSS Containing Block 规则）
// 把我们的 `position: fixed` 锁死在对话框里。
export function PortraitLightbox({
  url,
  name,
  onClose
}: {
  url: string
  name: string
  onClose: () => void
}): React.ReactPortal | null {
  const overlayRef = useRef<HTMLDivElement>(null)

  // 稳定的 ref：保证 keydown 监听只挂一次，不会在父组件每次重渲染、
  // 产生新的 onClose 闭包时被反复重挂。
  const onCloseRef = useLatestRef(onClose)

  // 灯箱打开时把整个视口注册为可交互区，避免点击图片或背景时事件穿透到下层窗口。
  // 函数引用稳定下来，useInteractiveRegion 的 effect 不会每次渲染都重新订阅。
  const getLightboxRect = (): DOMRect => new DOMRect(0, 0, window.innerWidth, window.innerHeight)

  useInteractiveRegion('portrait-lightbox', overlayRef, getLightboxRect)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onCloseRef.current()
      }
    }

    window.addEventListener('keydown', onKey)

    return () => window.removeEventListener('keydown', onKey)
  }, [onCloseRef])

  if (typeof document === 'undefined') {
    return null
  }

  return createPortal(
    <div
      aria-label="点击关闭"
      className="fixed inset-0 z-[100] flex items-center justify-center p-6"
      onClick={onClose}
      ref={overlayRef}
      role="dialog"
      style={{ pointerEvents: 'auto', background: 'rgba(0,0,0,0.35)' }}
    >
      <button
        aria-label="关闭预览"
        className="block cursor-zoom-out rounded-2xl border-0 bg-transparent p-0"
        onClick={onClose}
        type="button"
      >
        <img alt={name} className="block max-h-[90vh] max-w-[90vw] rounded-2xl object-contain shadow-2xl" src={url} />
      </button>
    </div>,
    document.body
  )
}
