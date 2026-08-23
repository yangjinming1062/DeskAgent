import { useStore } from '@nanostores/react'
import { type PointerEvent, type ReactNode, useCallback, useEffect, useRef } from 'react'

import { $chatOpen } from '@/companion/chat-store'
import { useInteractiveRegion } from '@/companion/interactive-regions'

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

interface SpriteStageProps {
  children: ReactNode
  onTap?: () => void
  onDoubleTap?: () => void
  onContextMenu?: (e: React.MouseEvent) => void
  hidden?: boolean
}

// 12px 是为了避免触控板微抖动被误判为拖拽、把双击吞掉。
const DRAG_THRESHOLD = 12
const DOUBLE_TAP_MS = 320

// 一旦光标跨到另一块显示器，pointer capture 会持续投递跨视口坐标；
// 探测主进程的频率最多为此间隔。
const DISPLAY_SWITCH_PROBE_MS = 200

const SPRITE_REGION_ID = 'sprite-stage'

export function SpriteStage({
  children,
  onTap,
  onDoubleTap,
  onContextMenu,
  hidden = false
}: SpriteStageProps): React.JSX.Element {
  const mountRef = useRef<HTMLDivElement>(null)

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
  // 实时 3D 轮廓探测，通过 ref 同步以保证 region 的 hitTest 闭包稳定。
  const hit3DRef = useRef<((x: number, y: number) => boolean | null) | null>(null)

  useEffect(
    () =>
      $sprite3DHitTest.subscribe(fn => {
        hit3DRef.current = fn
      }),
    []
  )

  const pendingPosRef = useRef<{ x: number; y: number } | null>(null)
  const pendingVelRef = useRef<{ vx: number; vy: number } | null>(null)
  const dragRafRef = useRef<number | null>(null)
  const displayProbeAtRef = useRef(0)
  const lastDragPointRef = useRef<{ x: number; y: number } | null>(null)

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

  // 命中由 3D 实时轮廓探测决定：探测就绪后严格 miss → false；
  // 启动/加载阶段返回 null 以保留矩形兜底。
  const stageHitTest = useCallback((x: number, y: number): boolean => {
    return hit3DRef.current?.(x, y) ?? true
  }, [])

  useInteractiveRegion(SPRITE_REGION_ID, mountRef, stageRect, stageHitTest)

  useEffect(() => {
    return () => {
      if (dragRafRef.current !== null) {
        cancelAnimationFrame(dragRafRef.current)
        dragRafRef.current = null
      }
    }
  }, [])

  // 精灵窗口只占一块显示器；要把精灵搬到另一块显示器上就要移动窗口。
  // 主进程会把窗口对齐到光标所在显示器并返回两个窗口原点。
  // 只有精灵的 POSITION 需要按原点 delta 平移——拖拽参考点不能动：
  // 切换后到达的 pointer 事件在 NEW 视口空间里（client 本身就跳过了同样的 delta），
  // 所以 origin + (client - start) 会自然产出平移后的值；再平移 start 反而
  // 会把精灵钉在旧视口坐标上、甩到新显示器边缘。
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

        // 窗口跳转前抓到的坐标是旧空间，跳转后是新空间；两者相差原点 delta
        // （几百像素），但主进程读取光标之后光标只动了几个像素。
        // 如果最新的拖拽点已经在新空间，拖拽公式自己就能算出平移后的位置——
        // 再平移一次会让 delta 在一帧内被双重应用。
        const point = d?.moved ? { x: d.lastX, y: d.lastY } : lastDragPointRef.current

        if (
          point &&
          Math.hypot(point.x - (cursor.x - to.x), point.y - (cursor.y - to.y)) <=
            Math.hypot(point.x - (cursor.x - from.x), point.y - (cursor.y - from.y))
        ) {
          return
        }

        const dragging = d?.moved === true

        // 拖拽释放比显示器切换早到——也要重映射静止位置，否则精灵会停在旧视口
        // 坐标上（新显示器上看不见）。自主移动已经算出新空间位置时跳过。
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

    // 只在按下左键时捕获
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

    // 只有左键松开触发 tap / double-tap；右键打开右键菜单且不触发戳击反应
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
