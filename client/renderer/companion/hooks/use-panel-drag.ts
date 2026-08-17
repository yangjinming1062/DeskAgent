import { type PointerEvent as ReactPointerEvent, useMemo, useRef } from 'react'

export interface PanelDragBind {
  onPointerCancel: (e: ReactPointerEvent<HTMLElement>) => void
  onPointerDown: (e: ReactPointerEvent<HTMLElement>) => void
  onPointerMove: (e: ReactPointerEvent<HTMLElement>) => void
  onPointerUp: (e: ReactPointerEvent<HTMLElement>) => void
}

// Header-drag for sprite-window panels: translate3d (no re-render per move),
// offset persisted to localStorage so the position survives a restart.
// getBoundingClientRect() includes the transform, so useInteractiveRegion
// hit-testing follows the dragged panel for free. The panel is a lazy getter
// (not a RefObject) so the transform write below can't be traced back to a
// hook argument (react-compiler mutation guard).
export function usePanelDrag(
  storageKey: string,
  getPanel: () => HTMLElement | null
): {
  bind: PanelDragBind
  storedOffset: { dx: number; dy: number } | null
} {
  const storedOffset = useMemo(() => {
    if (typeof localStorage === 'undefined') {
      return null
    }

    try {
      const raw = localStorage.getItem(storageKey)

      return raw ? (JSON.parse(raw) as { dx: number; dy: number }) : null
    } catch {
      return null
    }
  }, [storageKey])

  const offsetRef = useRef<{ dx: number; dy: number }>(storedOffset ?? { dx: 0, dy: 0 })
  const dragRef = useRef<{ startX: number; startY: number; baseDx: number; baseDy: number } | null>(null)

  const onPointerDown = (e: ReactPointerEvent<HTMLElement>) => {
    // Only left-button drags; ignore middle/right click and modifier-hold.
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) {
      return
    }

    // Don't start a drag when the user actually clicked a control inside the handle.
    if ((e.target as HTMLElement).closest('button, input, textarea, select, a, [role="button"]')) {
      return
    }

    e.currentTarget.setPointerCapture(e.pointerId)
    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      baseDx: offsetRef.current.dx,
      baseDy: offsetRef.current.dy
    }
  }

  const onPointerMove = (e: ReactPointerEvent<HTMLElement>) => {
    const d = dragRef.current

    if (!d) {
      return
    }

    const next = { dx: d.baseDx + (e.clientX - d.startX), dy: d.baseDy + (e.clientY - d.startY) }
    offsetRef.current = next

    const panel = getPanel()

    if (panel) {
      panel.style.transform = `translate3d(${next.dx}px, ${next.dy}px, 0)`
    }
  }

  const endDrag = (e: ReactPointerEvent<HTMLElement>) => {
    if (!dragRef.current) {
      return
    }

    e.currentTarget.releasePointerCapture(e.pointerId)
    dragRef.current = null

    if (typeof localStorage !== 'undefined') {
      try {
        localStorage.setItem(storageKey, JSON.stringify(offsetRef.current))
      } catch {
        /* private mode: in-memory only */
      }
    }
  }

  return {
    bind: {
      onPointerCancel: endDrag,
      onPointerDown,
      onPointerMove,
      onPointerUp: endDrag
    },
    storedOffset
  }
}
