import { type PointerEvent, useRef } from 'react'

import { handleDragEndInteraction } from '../interaction'
import { $spatialPos, endDragAt, startDrag, updateDragPosition } from '../spatial'

export interface AssemblyDragBind {
  onPointerCancel: (e: PointerEvent<HTMLElement>) => void
  onPointerDown: (e: PointerEvent<HTMLElement>) => void
  onPointerMove: (e: PointerEvent<HTMLElement>) => void
  onPointerUp: (e: PointerEvent<HTMLElement>) => void
}

/**
 * 伙伴 + 对话窗口组合体拖拽 Hook：
 * 挂载到对话坞（ChatDock）的拖拽把手（如左侧形象栏、顶部标题栏）上。
 * 拖动时直接驱动 $spatialPos，与 SpriteStage 拖动精灵共享同一位置源与视口钳制，
 * 实现「拖动任意一个，整体移动」的不变性。
 */
export function useAssemblyDrag(): {
  bind: AssemblyDragBind
} {
  const dragRef = useRef<{
    startX: number
    startY: number
    originX: number
    originY: number
    moved: boolean
  } | null>(null)

  const onPointerDown = (e: PointerEvent<HTMLElement>): void => {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) {
      return
    }

    if ((e.target as HTMLElement).closest('button, input, textarea, select, a, [role="button"]')) {
      return
    }

    e.currentTarget.setPointerCapture(e.pointerId)
    const cur = $spatialPos.get()
    dragRef.current = {
      moved: false,
      originX: cur.x,
      originY: cur.y,
      startX: e.clientX,
      startY: e.clientY
    }
  }

  const onPointerMove = (e: PointerEvent<HTMLElement>): void => {
    const d = dragRef.current

    if (!d) {
      return
    }

    const dx = e.clientX - d.startX
    const dy = e.clientY - d.startY

    if (!d.moved && Math.hypot(dx, dy) > 4) {
      d.moved = true
      startDrag()
    }

    if (d.moved) {
      updateDragPosition({
        x: Math.round(d.originX + dx),
        y: Math.round(d.originY + dy)
      })
    }
  }

  const endDrag = (e: PointerEvent<HTMLElement>): void => {
    const d = dragRef.current

    if (!d) {
      return
    }

    try {
      if (e.currentTarget.hasPointerCapture(e.pointerId)) {
        e.currentTarget.releasePointerCapture(e.pointerId)
      }
    } catch {
      /* 忽略释放失败 */
    }

    dragRef.current = null

    if (d.moved) {
      endDragAt($spatialPos.get())
      handleDragEndInteraction()
    }
  }

  return {
    bind: {
      onPointerCancel: endDrag,
      onPointerDown,
      onPointerMove,
      onPointerUp: endDrag
    }
  }
}
