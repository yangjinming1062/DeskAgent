import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import { useInteractiveRegion } from '@/companion/interactive-regions'
import { useLatestRef } from '@/shared/hooks/use-latest-ref'

import type { PortraitEntry, SeedUrls } from '../portrait-store'

// Extracts of the four small JSX components that were co-located inside
// onboarding-flow.tsx. All take their inputs as props — no shared module
// state — so they're trivially testable in isolation.

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
    <button
      className={`rounded-full border px-3 py-1 text-xs transition ${active ? 'border-white/60 bg-white/25' : 'border-white/20 bg-white/5 hover:bg-white/15'}`}
      onClick={onClick}
      type="button"
    >
      {label}
    </button>
  )
}

export function PortraitPanel({
  avatarUrl,
  seedUrls,
  name,
  hint,
  step = 'fullbody',
  introHint,
  history,
  selectedIdx,
  onSelectEntry,
  onRegenerateView,
  busyView
}: {
  avatarUrl: string | null
  seedUrls?: SeedUrls | null
  name: string
  hint: string | null
  step?: 'avatar' | 'fullbody'
  introHint?: string | null
  history?: PortraitEntry[]
  selectedIdx?: number
  onSelectEntry?: (idx: number) => void
  onRegenerateView?: (view: 'front' | 'right' | 'back') => void
  busyView?: string | null
}): React.JSX.Element {
  const [zoomedUrl, setZoomedUrl] = useState<string | null>(null)

  const gallery =
    history && history.length > 1 && onSelectEntry ? (
      <HistoryGallery
        entries={history}
        onSelect={onSelectEntry}
        selectedIdx={selectedIdx ?? history.length - 1}
        step={step}
      />
    ) : null

  if (step === 'avatar') {
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

  return (
    <div className="flex flex-col items-center gap-2">
      <div className="flex justify-center gap-2">
        <PortraitThumb
          label="正面"
          name={name}
          onRegenerate={onRegenerateView ? () => onRegenerateView('front') : undefined}
          onZoom={seedUrls?.front ? () => setZoomedUrl(seedUrls.front) : undefined}
          regenerating={busyView === 'front'}
          size="sm"
          url={seedUrls?.front ?? null}
        />
        <PortraitThumb
          label="右侧"
          name={name}
          onRegenerate={onRegenerateView ? () => onRegenerateView('right') : undefined}
          onZoom={seedUrls?.right ? () => setZoomedUrl(seedUrls.right) : undefined}
          regenerating={busyView === 'right'}
          size="sm"
          url={seedUrls?.right ?? null}
        />
        <PortraitThumb
          label="背面"
          name={name}
          onRegenerate={onRegenerateView ? () => onRegenerateView('back') : undefined}
          onZoom={seedUrls?.back ? () => setZoomedUrl(seedUrls.back) : undefined}
          regenerating={busyView === 'back'}
          size="sm"
          url={seedUrls?.back ?? null}
        />
      </div>
      {gallery}
      {hint && <p className="text-xs text-rose-300/90">{hint}</p>}
      {zoomedUrl && <PortraitLightbox name={name} onClose={() => setZoomedUrl(null)} url={zoomedUrl} />}
    </div>
  )
}

function HistoryGallery({
  entries,
  selectedIdx,
  onSelect,
  step
}: {
  entries: PortraitEntry[]
  selectedIdx: number
  onSelect: (idx: number) => void
  step: 'avatar' | 'fullbody'
}): React.JSX.Element {
  return (
    <div className="mt-1 flex justify-center gap-1.5">
      {entries.map((entry, idx) => {
        const thumb = step === 'avatar' ? entry.portraitUrl : entry.seedUrls?.front

        return (
          <button
            className={`overflow-hidden rounded-md border transition ${
              idx === selectedIdx ? 'border-white/80' : 'border-white/15 opacity-60 hover:opacity-90'
            }`}
            key={idx}
            onClick={() => onSelect(idx)}
            type="button"
          >
            {thumb ? (
              <img alt="" className="h-10 w-10 object-cover" src={thumb} />
            ) : (
              <div className="grid h-10 w-10 place-items-center text-[10px] text-white/30">—</div>
            )}
          </button>
        )
      })}
    </div>
  )
}

function PortraitThumb({
  label,
  name,
  onZoom,
  onRegenerate,
  regenerating,
  url,
  size = 'sm'
}: {
  label: string
  name: string
  onZoom: (() => void) | undefined
  onRegenerate?: () => void
  regenerating?: boolean
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
          {onRegenerate && !regenerating && (
            <button
              aria-label="重新生成此视角"
              className="absolute right-1 top-1 grid h-6 w-6 place-items-center rounded-full bg-black/50 text-white/60 opacity-0 transition hover:text-white group-hover:opacity-100"
              onClick={e => {
                e.stopPropagation()
                onRegenerate()
              }}
              type="button"
            >
              ↻
            </button>
          )}
          {regenerating && (
            <div className={`absolute inset-0 grid ${sizeClass} place-items-center rounded-xl bg-black/50`}>
              <div className="h-4 w-4 animate-spin rounded-full border border-white/30 border-t-white/80" />
            </div>
          )}
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

// Lightbox sized to the portrait itself — no full-screen dark overlay. The
// image is the window; the X is overlaid on its top-right corner; a faint
// transparent backdrop catches outside clicks. Rendered via createPortal at
// document.body so the onboarding container's `backdrop-filter` doesn't trap
// our `position: fixed` inside the small dialog box (per CSS Containing Block
// rules).
function PortraitLightbox({
  url,
  name,
  onClose
}: {
  url: string
  name: string
  onClose: () => void
}): React.ReactPortal | null {
  const overlayRef = useRef<HTMLDivElement>(null)

  // Stable ref so the keydown listener attaches once, not on every parent
  // re-render that creates a fresh onClose closure.
  const onCloseRef = useLatestRef(onClose)

  // Register the full viewport as an interactive region while the lightbox is open
  // so clicks on the image and its backdrop don't pass through to the windows below.
  // Stabilized so useInteractiveRegion's effect doesn't re-subscribe on every render.
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
