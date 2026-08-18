import { useStore } from '@nanostores/react'
import { type PointerEvent, type ReactNode, useCallback, useEffect, useRef } from 'react'

import { $chatOpen } from '@/companion/chat-store'
import { isPointInteractive, setCaptureProbe, useInteractiveRegion } from '@/companion/interactive-regions'

import { $sprite3DHitTest } from '../3d/silhouette-hit'
import { handleDragEndInteraction, handleHoverInteraction } from '../interaction'
import {
  $homePosition,
  $spatialLocomotion,
  $spatialPos,
  $spatialScale,
  cancelMovement,
  endDragAt,
  getBaseSpriteHeight,
  getBaseSpriteWidth,
  startDrag,
  updateDragPosition
} from '../spatial'
import { type SpriteHit, spriteHitTest } from '../static-sprite/sprite-hitmap'

interface SpriteStageProps {
  children: ReactNode
  onTap?: () => void
  onDoubleTap?: () => void
  onContextMenu?: (e: React.MouseEvent) => void
  spriteHit?: SpriteHit | null
  hidden?: boolean
}

// 12px keeps trackpad micro-jitter from misclassifying a double-tap as a drag.
const DRAG_THRESHOLD = 12
const DOUBLE_TAP_MS = 320

// Pointer capture keeps delivering coordinates past the viewport edge once the cursor
// crosses onto another display; probe main at most this often for a display handoff.
const DISPLAY_SWITCH_PROBE_MS = 200

const SPRITE_REGION_ID = 'sprite-stage'

export function SpriteStage({
  children,
  onTap,
  onDoubleTap,
  onContextMenu,
  spriteHit,
  hidden = false
}: SpriteStageProps): React.JSX.Element {
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
  const hitRef = useRef<SpriteHit | null>(null)
  // Live 3D silhouette probe, synced through a ref for the same
  // closure-stability reason as hitRef above.
  const hit3DRef = useRef<((x: number, y: number) => boolean | null) | null>(null)

  useEffect(
    () =>
      $sprite3DHitTest.subscribe(fn => {
        hit3DRef.current = fn
      }),
    []
  )

  const pendingToggleRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const pendingPosRef = useRef<{ x: number; y: number } | null>(null)
  const pendingVelRef = useRef<{ vx: number; vy: number } | null>(null)
  const dragRafRef = useRef<number | null>(null)
  const displayProbeAtRef = useRef(0)
  const lastDragPointRef = useRef<{ x: number; y: number } | null>(null)

  // 0-delay unignore on capture: setIgnoreMouseEvents(true, { forward: true }) does NOT forward mousedown/contextmenu to the renderer, so the window must be ungnored BEFORE the click arrives — mousemove is the only signal that reaches us.
  const captureImmediate = useCallback(() => {
    if (pendingToggleRef.current) {
      clearTimeout(pendingToggleRef.current)
      pendingToggleRef.current = null
    }

    void window.spiritagent.sprite.setIgnoreMouseEvents({ ignore: false })
  }, [])

  // 100 ms debounce on release: prevents boundary jitter from repeatedly flipping the window between interactive and click-through during fast mouse sweeps.
  const releaseDebounced = useCallback(() => {
    if (pendingToggleRef.current) {
      clearTimeout(pendingToggleRef.current)
    }

    pendingToggleRef.current = setTimeout(() => {
      pendingToggleRef.current = null
      void window.spiritagent.sprite.setIgnoreMouseEvents({ ignore: true, forward: true })
    }, 100)
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

  // spriteHit flows through a ref so the region's hitTest closure stays stable
  // — otherwise every image swap would unregister/re-register the region.
  useEffect(() => {
    hitRef.current = spriteHit ?? null
  }, [spriteHit])

  const stageRect = useCallback(
    (el: HTMLElement): DOMRect | null => {
      if (hidden) {
        return null
      }

      const rect = el.getBoundingClientRect()

      if (rect.width === 0 || rect.height === 0) {
        return null
      }

      return rect
    },
    [hidden]
  )

  // Static hitmap refines while its image is on display; 3D mode falls to
  // the live silhouette probe (strict miss → false once warm; null during
  // boot/load keeps the rect fallback).
  const stageHitTest = useCallback((x: number, y: number): boolean => {
    const hit = hitRef.current

    if (hit) {
      return spriteHitTest(hit, x, y)
    }

    return hit3DRef.current?.(x, y) ?? true
  }, [])

  useInteractiveRegion(SPRITE_REGION_ID, mountRef, stageRect, stageHitTest)

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

      // Fast-path: immediately un-ignore without waiting for next rAF frame when entering interactive pixels
      if (isPointInteractive(e.clientX, e.clientY)) {
        capture()
      } else if (moveRafId === null) {
        moveRafId = requestAnimationFrame(flushMove)
      }
    }

    const probe = () => {
      const p = lastPointRef.current

      if (p && !dragRef.current) {
        if (isPointInteractive(p.x, p.y)) {
          capture()
        } else {
          release()
        }
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

  // The sprite window lives on a single display; moving the sprite to another monitor
  // means moving the window. Main snaps it onto the cursor's display and returns both
  // window origins. Only the sprite POSITION shifts by the origin delta — the drag
  // reference points must not: pointer events after the switch arrive in the NEW
  // viewport space (client itself jumps by the same delta), so origin + (client −
  // start) keeps producing the shifted value on its own. Shifting start too pins the
  // sprite to its old-viewport coordinates and flings it to the far edge of the new
  // display.
  const probeDisplaySwitch = useCallback((): void => {
    const now = performance.now()

    if (now - displayProbeAtRef.current < DISPLAY_SWITCH_PROBE_MS) {
      return
    }

    displayProbeAtRef.current = now

    void window.spiritagent.sprite
      .moveToCursorDisplay()
      .then(switched => {
        if (!switched) {
          return
        }

        const { cursor, from, to } = switched
        const dx = from.x - to.x
        const dy = from.y - to.y
        const d = dragRef.current

        // Pointer coords captured before the window jump are old-space, after it
        // new-space; they differ by the origin delta (hundreds of px) while the cursor
        // moved only a few px since main read it. If the latest drag point already sits
        // in the new space, the drag formula recomputes the shifted position on its
        // own — shifting again would double-apply the delta for a frame.
        const point = d?.moved ? { x: d.lastX, y: d.lastY } : lastDragPointRef.current

        if (
          point &&
          Math.hypot(point.x - (cursor.x - to.x), point.y - (cursor.y - to.y)) <=
            Math.hypot(point.x - (cursor.x - from.x), point.y - (cursor.y - from.y))
        ) {
          return
        }

        const dragging = d?.moved === true

        // Released before the handoff landed — remap the resting position too, or the
        // sprite stays parked in old-viewport coordinates (off-screen on the new
        // display). Skip when an autonomous move already recomputed a new-space one.
        if (!dragging && ($spatialLocomotion.get() !== 'still' || $chatOpen.get())) {
          return
        }

        if (pendingPosRef.current) {
          pendingPosRef.current.x += dx
          pendingPosRef.current.y += dy
        }

        const pos = $spatialPos.get()
        const next = { x: pos.x + dx, y: pos.y + dy }
        $spatialPos.set(next)

        if (!dragging) {
          $homePosition.set(next)
          void window.spiritagent.sprite.setPosition(next)
        }
      })
      .catch(() => {})
  }, [])

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (hidden) {
      return
    }

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
    if (hidden) {
      return
    }

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
      if (e.clientX < 0 || e.clientX > window.innerWidth || e.clientY < 0 || e.clientY > window.innerHeight) {
        probeDisplaySwitch()
      }

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
    if (hidden) {
      return
    }

    ;(e.currentTarget as Element).releasePointerCapture?.(e.pointerId)
    const drag = dragRef.current
    dragRef.current = null
    lastDragPointRef.current = drag?.moved ? { x: drag.lastX, y: drag.lastY } : null

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

    // Only left button releases trigger tap / double-tap; right-clicks open the context menu without activating a poke reaction
    if (e.button !== 0) {
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
        className={`absolute transition-opacity duration-200 ${hidden ? 'pointer-events-none opacity-0 invisible' : 'opacity-100'}`}
        onContextMenu={e => {
          if (hidden) {
            return
          }

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
          pointerEvents: hidden ? 'none' : 'auto',
          touchAction: 'none',
          visibility: hidden ? 'hidden' : 'visible',
          opacity: hidden ? 0 : 1,
          transform: `translate3d(${pos.x}px, ${pos.y}px, 0px) scale(${scale})`,
          transformOrigin: 'top left',
          willChange: 'transform, opacity'
        }}
      >
        {children}
      </div>
    </div>
  )
}
