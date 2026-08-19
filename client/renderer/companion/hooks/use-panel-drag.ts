import { type PointerEvent as ReactPointerEvent, useMemo, useRef } from 'react'

export interface PanelDragBind {
  onPointerCancel: (e: ReactPointerEvent<HTMLElement>) => void
  onPointerDown: (e: ReactPointerEvent<HTMLElement>) => void
  onPointerMove: (e: ReactPointerEvent<HTMLElement>) => void
  onPointerUp: (e: ReactPointerEvent<HTMLElement>) => void
}

// 精灵窗口面板的标题栏拖拽：translate3d（每次移动不触发重渲染），
// 偏移量持久化到 localStorage，位置重启后依然保留。
// getBoundingClientRect() 会包含 transform，因此 useInteractiveRegion
// 的命中测试自动跟随拖拽后的面板。panel 是惰性 getter（而非 RefObject），
// 这样下面对 transform 的写入不会回溯到 hook 参数（react-compiler 变更守卫）。
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
    // 仅响应左键拖拽；忽略中键/右键点击以及带修饰键的拖拽。
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) {
      return
    }

    // 当用户实际点中拖拽柄里的控件时，不要开启拖拽。
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
        /* 无痕模式：仅内存有效 */
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
