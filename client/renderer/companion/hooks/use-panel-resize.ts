import { type PointerEvent, useEffect, useMemo, useRef, useState } from 'react'

export type ResizeDirection = 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw'

interface PanelSize {
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
    onPointerCancel: (e: PointerEvent<HTMLElement>) => void
    onPointerDown: (e: PointerEvent<HTMLElement>) => void
    onPointerMove: (e: PointerEvent<HTMLElement>) => void
    onPointerUp: (e: PointerEvent<HTMLElement>) => void
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

        // Math.min/max 对 NaN 直接透传——非数值的持久化尺寸回退默认，避免
        // width: NaN 让内联尺寸失效、面板塌成左上角的零定长块。
        const clamp = (value: unknown, fallback: number): number =>
          typeof value === 'number' && Number.isFinite(value) ? value : fallback

        return {
          width: Math.min(maxSize.width, Math.max(minSize.width, clamp(parsed.width, defaultSize.width))),
          height: Math.min(maxSize.height, Math.max(minSize.height, clamp(parsed.height, defaultSize.height)))
        }
      }
    } catch {
      /* 回退 */
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

  const handlePointerDown = (dir: ResizeDirection, e: PointerEvent<HTMLElement>) => {
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

  const handlePointerMove = (e: PointerEvent<HTMLElement>) => {
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

    // 水平
    if (dir.includes('e')) {
      newWidth = Math.min(maxSize.width, Math.max(minSize.width, startWidth + deltaX))
    } else if (dir.includes('w')) {
      newWidth = Math.min(maxSize.width, Math.max(minSize.width, startWidth - deltaX))
      newDx = startDx + (startWidth - newWidth)
    }

    // 垂直
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

  const handlePointerUp = (e: PointerEvent<HTMLElement>) => {
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

        // 左侧/顶部边缘的宽度/高度变化时同步更新偏移
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
        /* 存储错误 */
      }
    }
  }

  const getResizeHandleProps = (dir: ResizeDirection) => ({
    onPointerDown: (e: PointerEvent<HTMLElement>) => handlePointerDown(dir, e),
    onPointerMove: handlePointerMove,
    onPointerUp: handlePointerUp,
    onPointerCancel: handlePointerUp
  })

  return {
    size,
    getResizeHandleProps
  }
}
