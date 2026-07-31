import { useStore } from '@nanostores/react'
import { type ReactNode, type PointerEvent as ReactPointerEvent, useEffect, useRef, useState } from 'react'

import { $chatOpen } from '@/companion/chat-store'
import { setSpritePosition } from '@/companion/companion-store'

import { handleDragEndInteraction, handleHoverInteraction } from '../interaction'

// The sprite window is screen-sized, transparent, and click-through by default
// (main sets setIgnoreMouseEvents(true, {forward:true})). mouse-move is still
// forwarded, so we hit-test it against the sprite's rect and request capture
// (setIgnoreMouseEvents(false)) only while the cursor is over it — letting the
// desktop show through everywhere else. Tap vs drag is resolved by movement.
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

export function SpriteStage({ children, onTap, onDoubleTap, onContextMenu }: SpriteStageProps) {
  const mountRef = useRef<HTMLDivElement>(null)
  const capturedRef = useRef(false)
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
    const onMove = (e: MouseEvent) => {
      if (capturedRef.current) {return}
      const el = mountRef.current

      if (!el) {return}
      const r = el.getBoundingClientRect()

      if (e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom) {
        capture()
      }
    }

    window.addEventListener('mousemove', onMove)

    return () => window.removeEventListener('mousemove', onMove)
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
        onMouseLeave={() => {
          if (!dragRef.current) {release()}
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
