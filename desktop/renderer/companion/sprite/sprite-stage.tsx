import { useStore } from '@nanostores/react'
import { type ReactNode, type PointerEvent as ReactPointerEvent, useEffect, useRef, useState } from 'react'

import { $chatOpen } from '@/companion/chat-store'
import { setSpritePosition } from '@/companion/companion-store'
import { isPointInteractive, registerInteractiveRegion, setCaptureProbe, unregisterInteractiveRegion } from '@/companion/interactive-regions'

import { handleDragEndInteraction, handleHoverInteraction } from '../interaction'

// Hit-test the forwarded mousemove against registered interactive regions
// (see companion/interactive-regions.ts); capture only while the cursor is
// over one. Tap vs drag is resolved by movement.
interface SpriteStageProps {
  children: ReactNode
  onTap?: () => void
  onDoubleTap?: () => void
  onContextMenu?: (e: React.MouseEvent) => void
}

const REST_MARGIN = 24
const EGG_W = 160
const EGG_H = 184
const DRAG_THRESHOLD = 6
const DOUBLE_TAP_MS = 320
// Covers the sprite's CSS glow halos that overflow the inner box: egg-glow
// 150% (≈40px/side), companion-glow 170% (≈56px), sil-glow 170% of 180 (≈63px).
const HALO_PAD = 70

const SPRITE_REGION_ID = 'sprite-stage'

export function SpriteStage({ children, onTap, onDoubleTap, onContextMenu }: SpriteStageProps) {
  const mountRef = useRef<HTMLDivElement>(null)
  const capturedRef = useRef(false)
  const lastPointRef = useRef<{ x: number; y: number } | null>(null)
  const dragRef = useRef<{ startX: number; startY: number; originX: number; originY: number; moved: boolean } | null>(null)
  const lastTapRef = useRef(0)
  const chatOpen = useStore($chatOpen)

  const [pos, setPos] = useState<{ x: number; y: number }>(() => ({
    x: Math.max(REST_MARGIN, window.innerWidth - EGG_W - REST_MARGIN),
    y: Math.max(REST_MARGIN, window.innerHeight - EGG_H - REST_MARGIN)
  }))

  // Plan §4.1 "对话发生在角色身边": when chat opens the sprite joins the
  // dialog in the centered column (upper area, dialog below). Voice-call mode
  // leaves the sprite in place (ambient). Restores the dragged position on close.
  const displayPos = chatOpen
    ? { x: Math.round((window.innerWidth - EGG_W) / 2), y: Math.round(window.innerHeight * 0.16) }
    : pos

  const capture = () => {
    if (capturedRef.current) {return}
    capturedRef.current = true
    handleHoverInteraction()
    void window.deskagent.sprite.setIgnoreMouseEvents({ ignore: false })
  }

  const release = () => {
    if (!capturedRef.current) {return}
    capturedRef.current = false
    void window.deskagent.sprite.setIgnoreMouseEvents({ ignore: true, forward: true })
  }

  useEffect(() => {
    registerInteractiveRegion(SPRITE_REGION_ID, () => {
      const rect = mountRef.current?.getBoundingClientRect() ?? null

      if (!rect || rect.width === 0 || rect.height === 0) {return null}

      return new DOMRect(rect.left - HALO_PAD, rect.top - HALO_PAD, rect.width + 2 * HALO_PAD, rect.height + 2 * HALO_PAD)
    })

    return () => unregisterInteractiveRegion(SPRITE_REGION_ID)
  }, [])

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      // lastPointRef only matters while uncaptured (probe path); skip the
      // allocation once we already own the window.
      if (!capturedRef.current) {lastPointRef.current = { x: e.clientX, y: e.clientY }}

      // Two-way: cursor inside a registered region → capture; outside all →
      // release. Mouseleave alone wouldn't catch "cursor moves within the
      // window but exits the sprite" — that's the whole point of the
      // region hit-test, so do it on every move.
      if (isPointInteractive(e.clientX, e.clientY)) {capture()}
      else if (!dragRef.current) {release()}
    }

    const probe = () => {
      const p = lastPointRef.current

      if (p && isPointInteractive(p.x, p.y)) {capture()}
    }

    window.addEventListener('mousemove', onMove)
    setCaptureProbe(probe)

    return () => {
      window.removeEventListener('mousemove', onMove)
      setCaptureProbe(null)
      release()
    }
  }, [])

  const onPointerDown = (e: ReactPointerEvent) => {
    capture()
    ;(e.currentTarget as Element).setPointerCapture?.(e.pointerId)
    dragRef.current = { startX: e.clientX, startY: e.clientY, originX: pos.x, originY: pos.y, moved: false }
  }

  const onPointerMove = (e: ReactPointerEvent) => {
    const drag = dragRef.current

    if (!drag) {return}
    const dx = e.clientX - drag.startX
    const dy = e.clientY - drag.startY

    if (!drag.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) {return}
    drag.moved = true
    setPos({ x: drag.originX + dx, y: drag.originY + dy })
  }

  const endDrag = () => {
    const drag = dragRef.current
    dragRef.current = null

    return drag
  }

  const onPointerUp = (e: ReactPointerEvent) => {
    ;(e.currentTarget as Element).releasePointerCapture?.(e.pointerId)
    const drag = endDrag()

    if (drag?.moved) {
      setSpritePosition(pos)
      void window.deskagent.sprite.setPosition(pos)
      handleDragEndInteraction()
      release()

      return
    }

    const now = Date.now()

    if (onDoubleTap && now - lastTapRef.current < DOUBLE_TAP_MS) {
      lastTapRef.current = 0
      onDoubleTap()
    } else {
      lastTapRef.current = now
      onTap?.()
    }
  }

  return (
    <div className="fixed inset-0" style={{ pointerEvents: 'none' }}>
      <div
        className="absolute"
        onContextMenu={e => {
          e.preventDefault()
          onContextMenu?.(e)
        }}
        onPointerCancel={onPointerUp}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        ref={mountRef}
        style={{ left: displayPos.x, top: displayPos.y, pointerEvents: 'auto', touchAction: 'none' }}
      >
        {children}
      </div>
    </div>
  )
}