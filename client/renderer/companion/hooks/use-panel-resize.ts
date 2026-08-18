import { type PointerEvent as ReactPointerEvent, useEffect, useMemo, useRef, useState } from 'react'

export type ResizeDirection = 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw'

export interface PanelSize {
  width: number
  height: number
}

interface UsePanelResizeOptions {
  sizeStorageKey: string
  offsetStorageKey: string
  defaultSize: PanelSize
  minSize?: PanelSize
  maxSize?: PanelSize
  getPanel: () => HTMLElement | null
}

export function usePanelResize({
  sizeStorageKey,
  offsetStorageKey,
  defaultSize,
  minSize = { width: 560, height: 400 },
  maxSize = { width: 1400, height: 900 },
  getPanel
}: UsePanelResizeOptions): {
  size: PanelSize
  getResizeHandleProps: (dir: ResizeDirection) => {
    onPointerDown: (e: ReactPointerEvent<HTMLElement>) => void
    onPointerMove: (e: ReactPointerEvent<HTMLElement>) => void
    onPointerUp: (e: ReactPointerEvent<HTMLElement>) => void
    onPointerCancel: (e: ReactPointerEvent<HTMLElement>) => void
  }
} {
  const initialSize = useMemo(() => {
    if (typeof localStorage === 'undefined') {
      return defaultSize
    }

    try {
      const raw = localStorage.getItem(sizeStorageKey)

      if (raw) {
        const parsed = JSON.parse(raw) as Partial<PanelSize>

        return {
          width: Math.min(maxSize.width, Math.max(minSize.width, parsed.width ?? defaultSize.width)),
          height: Math.min(maxSize.height, Math.max(minSize.height, parsed.height ?? defaultSize.height))
        }
      }
    } catch {
      /* fallback */
    }

    return defaultSize
  }, [sizeStorageKey, defaultSize, minSize, maxSize])

  const [size, setSize] = useState<PanelSize>(initialSize)
  const sizeRef = useRef<PanelSize>(initialSize)

  useEffect(() => {
    sizeRef.current = size
  }, [size])

  const resizeStateRef = useRef<{
    dir: ResizeDirection
    startX: number
    startY: number
    startWidth: number
    startHeight: number
    startDx: number
    startDy: number
  } | null>(null)

  const getStoredOffset = (): { dx: number; dy: number } => {
    if (typeof localStorage === 'undefined') {
      return { dx: 0, dy: 0 }
    }

    try {
      const raw = localStorage.getItem(offsetStorageKey)

      return raw ? (JSON.parse(raw) as { dx: number; dy: number }) : { dx: 0, dy: 0 }
    } catch {
      return { dx: 0, dy: 0 }
    }
  }

  const handlePointerDown = (dir: ResizeDirection, e: ReactPointerEvent<HTMLElement>) => {
    if (e.button !== 0) {
      return
    }

    e.preventDefault()
    e.stopPropagation()
    e.currentTarget.setPointerCapture(e.pointerId)

    const offset = getStoredOffset()

    resizeStateRef.current = {
      dir,
      startX: e.clientX,
      startY: e.clientY,
      startWidth: sizeRef.current.width,
      startHeight: sizeRef.current.height,
      startDx: offset.dx,
      startDy: offset.dy
    }
  }

  const handlePointerMove = (e: ReactPointerEvent<HTMLElement>) => {
    const state = resizeStateRef.current

    if (!state) {
      return
    }

    const { dir, startX, startY, startWidth, startHeight, startDx, startDy } = state
    const deltaX = e.clientX - startX
    const deltaY = e.clientY - startY

    let newWidth = startWidth
    let newHeight = startHeight
    let newDx = startDx
    let newDy = startDy

    // Horizontal
    if (dir.includes('e')) {
      newWidth = Math.min(maxSize.width, Math.max(minSize.width, startWidth + deltaX))
    } else if (dir.includes('w')) {
      newWidth = Math.min(maxSize.width, Math.max(minSize.width, startWidth - deltaX))
      newDx = startDx + (startWidth - newWidth)
    }

    // Vertical
    if (dir.includes('s')) {
      newHeight = Math.min(maxSize.height, Math.max(minSize.height, startHeight + deltaY))
    } else if (dir.includes('n')) {
      newHeight = Math.min(maxSize.height, Math.max(minSize.height, startHeight - deltaY))
      newDy = startDy + (startHeight - newHeight)
    }

    const panel = getPanel()

    if (panel) {
      panel.style.width = `${newWidth}px`
      panel.style.height = `${newHeight}px`
      panel.style.transform = `translate3d(${newDx}px, ${newDy}px, 0)`
    }

    sizeRef.current = { width: newWidth, height: newHeight }
  }

  const handlePointerUp = (e: ReactPointerEvent<HTMLElement>) => {
    const state = resizeStateRef.current

    if (!state) {
      return
    }

    e.currentTarget.releasePointerCapture(e.pointerId)
    resizeStateRef.current = null

    const finalSize = sizeRef.current
    setSize(finalSize)

    if (typeof localStorage !== 'undefined') {
      try {
        localStorage.setItem(sizeStorageKey, JSON.stringify(finalSize))

        // Also update offset if width/height changed on left/top edge
        const panel = getPanel()

        if (panel?.style.transform) {
          const match = /translate3d\(([-\d.]+)px,\s*([-\d.]+)px/.exec(panel.style.transform)

          if (match) {
            localStorage.setItem(
              offsetStorageKey,
              JSON.stringify({
                dx: parseFloat(match[1]),
                dy: parseFloat(match[2])
              })
            )
          }
        }
      } catch {
        /* storage error */
      }
    }
  }

  const getResizeHandleProps = (dir: ResizeDirection) => ({
    onPointerDown: (e: ReactPointerEvent<HTMLElement>) => handlePointerDown(dir, e),
    onPointerMove: handlePointerMove,
    onPointerUp: handlePointerUp,
    onPointerCancel: handlePointerUp
  })

  return {
    size,
    getResizeHandleProps
  }
}
