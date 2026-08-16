import { useStore } from '@nanostores/react'
import { type PointerEvent, type ReactNode, useCallback, useEffect, useRef } from 'react'

import { isPointInteractive, setCaptureProbe, useInteractiveRegion } from '@/companion/interactive-regions'

import { handleDragEndInteraction, handleHoverInteraction } from '../interaction'
import { $spatialPos, $spatialScale, cancelMovement, endDragAt, startDrag, updateDragPosition } from '../spatial'

interface SpriteStageProps {
  children: ReactNode
  onTap?: () => void
  onDoubleTap?: () => void
  onContextMenu?: (e: React.MouseEvent) => void
}

// 12px keeps trackpad micro-jitter from misclassifying a double-tap as a drag.
const DRAG_THRESHOLD = 12
const DOUBLE_TAP_MS = 320
// Covers the sprite's CSS glow halos that overflow the inner box: companion-glow
// 170% (≈56px), sil-glow 170% of 180 (≈63px).
const HALO_PAD = 70

const SPRITE_REGION_ID = 'sprite-stage'

export function SpriteStage({ children, onTap, onDoubleTap, onContextMenu }: SpriteStageProps): React.JSX.Element {
  const mountRef = useRef<HTMLDivElement>(null)
  const capturedRef = useRef(false)
  const lastPointRef = useRef<{ x: number; y: number } | null>(null)

  const dragRef = useRef<{ startX: number; startY: number; originX: number; originY: number; moved: boolean } | null>(
    null
  )

  const lastTapRef = useRef(0)
  const pos = useStore($spatialPos)
  const scale = useStore($spatialScale)

  const pendingToggleRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const toggle = useCallback((enable: boolean) => {
    if (pendingToggleRef.current) {
      clearTimeout(pendingToggleRef.current)
    }

    pendingToggleRef.current = setTimeout(() => {
      pendingToggleRef.current = null

      if (enable) {
        void window.spiritagent.sprite.setIgnoreMouseEvents({ ignore: false })
      } else {
        void window.spiritagent.sprite.setIgnoreMouseEvents({ ignore: true, forward: true })
      }
    }, 50)
  }, [])

  const capture = useCallback(() => {
    if (capturedRef.current) {
      return
    }

    capturedRef.current = true
    handleHoverInteraction()
    toggle(true)
  }, [toggle])

  const release = useCallback(() => {
    if (!capturedRef.current) {
      return
    }

    capturedRef.current = false
    toggle(false)
  }, [toggle])

  useInteractiveRegion(SPRITE_REGION_ID, mountRef, el => {
    const rect = el.getBoundingClientRect()

    if (rect.width === 0 || rect.height === 0) {
      return null
    }

    return new DOMRect(rect.left - HALO_PAD, rect.top - HALO_PAD, rect.width + 2 * HALO_PAD, rect.height + 2 * HALO_PAD)
  })

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      lastPointRef.current = { x: e.clientX, y: e.clientY }

      if (isPointInteractive(e.clientX, e.clientY)) {
        capture()
      } else if (!dragRef.current) {
        release()
      }
    }

    const probe = () => {
      const p = lastPointRef.current

      if (p && isPointInteractive(p.x, p.y)) {
        capture()
      }
    }

    window.addEventListener('mousemove', onMove)
    setCaptureProbe(probe)

    return () => {
      window.removeEventListener('mousemove', onMove)
      setCaptureProbe(null)
      release()
    }
  }, [capture, release])

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    lastPointRef.current = { x: e.clientX, y: e.clientY }

    // Only capture when left-button is pressed
    if (e.button !== 0) {
      return
    }

    dragRef.current = { startX: e.clientX, startY: e.clientY, originX: pos.x, originY: pos.y, moved: false }
    cancelMovement()
  }

  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    lastPointRef.current = { x: e.clientX, y: e.clientY }
    const d = dragRef.current

    if (!d) {
      handleHoverInteraction()

      return
    }

    const dx = e.clientX - d.startX
    const dy = e.clientY - d.startY

    if (!d.moved && Math.hypot(dx, dy) > DRAG_THRESHOLD) {
      d.moved = true
      startDrag()
      e.currentTarget.setPointerCapture(e.pointerId)
      capturedRef.current = true
    }

    if (d.moved) {
      const nextX = Math.round(d.originX + dx)
      const nextY = Math.round(d.originY + dy)
      updateDragPosition({ x: nextX, y: nextY })
    }
  }

  const onPointerUp = (e: PointerEvent<HTMLDivElement>) => {
    ;(e.currentTarget as Element).releasePointerCapture?.(e.pointerId)
    const drag = dragRef.current
    dragRef.current = null

    if (drag?.moved) {
      endDragAt($spatialPos.get())
      handleDragEndInteraction()

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
        style={{ left: pos.x, top: pos.y, pointerEvents: 'auto', touchAction: 'none', transform: `scale(${scale})` }}
      >
        {children}
      </div>
    </div>
  )
}
