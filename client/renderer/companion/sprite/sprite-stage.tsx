import { useStore } from '@nanostores/react'
import { type PointerEvent, type ReactNode, useCallback, useEffect, useRef } from 'react'

import { isPointInteractive, setCaptureProbe, useInteractiveRegion } from '@/companion/interactive-regions'

import { handleDragEndInteraction, handleHoverInteraction } from '../interaction'
import {
  $spatialPos,
  $spatialScale,
  cancelMovement,
  endDragAt,
  getBaseSpriteHeight,
  getBaseSpriteWidth,
  startDrag,
  updateDragPosition
} from '../spatial'

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

  const dragRef = useRef<{
    startX: number
    startY: number
    originX: number
    originY: number
    moved: boolean
    lastX: number
    lastY: number
    lastTime: number
  } | null>(null)

  const lastTapRef = useRef(0)
  const pos = useStore($spatialPos)
  const scale = useStore($spatialScale)

  const pendingToggleRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const pendingPosRef = useRef<{ x: number; y: number } | null>(null)
  const pendingVelRef = useRef<{ vx: number; vy: number } | null>(null)
  const dragRafRef = useRef<number | null>(null)

  // 0-delay unignore on capture: setIgnoreMouseEvents(true, { forward: true }) does NOT forward mousedown/contextmenu to the renderer, so the window must be ungnored BEFORE the click arrives — mousemove is the only signal that reaches us.
  const captureImmediate = useCallback(() => {
    if (pendingToggleRef.current) {
      clearTimeout(pendingToggleRef.current)
      pendingToggleRef.current = null
    }

    void window.spiritagent.sprite.setIgnoreMouseEvents({ ignore: false })
  }, [])

  // 50 ms debounce on release: prevents boundary jitter from repeatedly flipping the window between interactive and click-through.
  const releaseDebounced = useCallback(() => {
    if (pendingToggleRef.current) {
      clearTimeout(pendingToggleRef.current)
    }

    pendingToggleRef.current = setTimeout(() => {
      pendingToggleRef.current = null
      void window.spiritagent.sprite.setIgnoreMouseEvents({ ignore: true, forward: true })
    }, 50)
  }, [])

  const capture = useCallback(() => {
    if (capturedRef.current) {
      return
    }

    capturedRef.current = true
    handleHoverInteraction()
    captureImmediate()
  }, [captureImmediate])

  const release = useCallback(() => {
    if (!capturedRef.current) {
      return
    }

    capturedRef.current = false
    releaseDebounced()
  }, [releaseDebounced])

  useInteractiveRegion(SPRITE_REGION_ID, mountRef, el => {
    const rect = el.getBoundingClientRect()

    if (rect.width === 0 || rect.height === 0) {
      return null
    }

    return new DOMRect(rect.left - HALO_PAD, rect.top - HALO_PAD, rect.width + 2 * HALO_PAD, rect.height + 2 * HALO_PAD)
  })

  useEffect(() => {
    // Coalesce mousemove to a single rAF tick — getBoundingClientRect() per region is layout-forcing, and 60+ Hz raw mousemove burns the frame budget on the resulting style-recalc.
    let moveRafId: number | null = null

    const flushMove = () => {
      moveRafId = null
      const p = lastPointRef.current

      if (!p || dragRef.current) {
        return
      }

      if (isPointInteractive(p.x, p.y)) {
        capture()
      } else {
        release()
      }
    }

    const onMove = (e: MouseEvent) => {
      lastPointRef.current = { x: e.clientX, y: e.clientY }

      if (moveRafId !== null) {
        return
      }

      moveRafId = requestAnimationFrame(flushMove)
    }

    const probe = () => {
      const p = lastPointRef.current

      if (p && !dragRef.current && isPointInteractive(p.x, p.y)) {
        capture()
      }
    }

    window.addEventListener('mousemove', onMove)
    setCaptureProbe(probe)

    return () => {
      window.removeEventListener('mousemove', onMove)
      setCaptureProbe(null)
      release()

      if (moveRafId !== null) {
        cancelAnimationFrame(moveRafId)
        moveRafId = null
      }

      if (dragRafRef.current !== null) {
        cancelAnimationFrame(dragRafRef.current)
        dragRafRef.current = null
      }
    }
  }, [capture, release])

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    lastPointRef.current = { x: e.clientX, y: e.clientY }

    // Only capture when left-button is pressed
    if (e.button !== 0) {
      return
    }

    const now = performance.now()
    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      originX: pos.x,
      originY: pos.y,
      moved: false,
      lastX: e.clientX,
      lastY: e.clientY,
      lastTime: now
    }
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
      const now = performance.now()
      const dt = Math.max(1, now - d.lastTime)
      const vx = (e.clientX - d.lastX) / dt
      const vy = (e.clientY - d.lastY) / dt
      d.lastX = e.clientX
      d.lastY = e.clientY
      d.lastTime = now

      const nextX = Math.round(d.originX + dx)
      const nextY = Math.round(d.originY + dy)

      pendingPosRef.current = { x: nextX, y: nextY }
      pendingVelRef.current = { vx, vy }

      if (dragRafRef.current === null) {
        dragRafRef.current = requestAnimationFrame(() => {
          dragRafRef.current = null

          if (pendingPosRef.current) {
            updateDragPosition(pendingPosRef.current, pendingVelRef.current ?? undefined)
          }
        })
      }
    }
  }

  const onPointerUp = (e: PointerEvent<HTMLDivElement>) => {
    ;(e.currentTarget as Element).releasePointerCapture?.(e.pointerId)
    const drag = dragRef.current
    dragRef.current = null

    if (dragRafRef.current !== null) {
      cancelAnimationFrame(dragRafRef.current)
      dragRafRef.current = null
    }

    if (pendingPosRef.current) {
      updateDragPosition(pendingPosRef.current, pendingVelRef.current ?? undefined)
      pendingPosRef.current = null
      pendingVelRef.current = null
    }

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

  const spriteW = getBaseSpriteWidth()
  const spriteH = getBaseSpriteHeight()

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
        style={{
          left: 0,
          top: 0,
          width: `${spriteW}px`,
          height: `${spriteH}px`,
          pointerEvents: 'auto',
          touchAction: 'none',
          transform: `translate3d(${pos.x}px, ${pos.y}px, 0px) scale(${scale})`,
          transformOrigin: 'top left',
          willChange: 'transform'
        }}
      >
        {children}
      </div>
    </div>
  )
}
