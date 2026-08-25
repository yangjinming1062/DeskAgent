import { useStore } from '@nanostores/react'
import { type PointerEvent, type ReactNode, useCallback, useEffect, useRef } from 'react'

import { $chatOpen, pushExternalAttachment } from '@/companion/chat-store'
import { $clipOverride, $spriteAction, setSpriteState } from '@/companion/companion-store'
import { useInteractiveRegion } from '@/companion/interactive-regions'

import { $sprite3DHitTest } from '../3d/silhouette-hit'
import {
  handleDizzyInteraction,
  handleDragEndInteraction,
  handleHoverInteraction,
  handlePetInteraction
} from '../interaction'
import { Mesh2DGestureTracker } from '../mesh2d/mesh2d-gestures'
import { $mesh2dHitmap } from '../mesh2d/mesh2d-store'
import { emitVfx, Mesh2DVfxOverlay } from '../mesh2d/mesh2d-vfx'
import {
  $homePosition,
  $isEdgeDocked,
  $spatialLocomotion,
  $spatialPos,
  $spatialScale,
  cancelMovement,
  endDragAt,
  getBaseSpriteHeight,
  getBaseSpriteWidth,
  startDrag,
  undockFromEdge,
  updateDragPosition
} from '../spatial'

interface SpriteStageProps {
  children: ReactNode
  onTap?: (nx: number, ny: number) => void
  onDoubleTap?: () => void
  onContextMenu?: (e: React.MouseEvent) => void
  hidden?: boolean
}

// 12px 是为了避免触控板微抖动被误判为拖拽、把双击吞掉。
const DRAG_THRESHOLD = 12
const DOUBLE_TAP_MS = 320
// 长按阈值（DESIGN §6.3）：按住未移动 ≥ 500ms 触发独立 long-press 事件，
// 与拖拽分离。SpriteStage 内长按时由 VFX 层接住，目前只暴露 onLongPress
// 回调供后续接角色动作（如抱头、撒娇）。
const LONG_PRESS_MS = 500

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
    pressedAt: number
  } | null>(null)

  const longPressTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

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

  // 命中按渲染路径精化：3D 走实时轮廓探测（读回未落地返回 null 保留矩形兜底），
  // 2D 走 2D 渲染层部件 bbox（PROTOCOL §1.4 契约）；两者都缺席（桌面蛋 / 加载空挡）
  // 才回退整矩形——否则 2D 模式下矩形空白区会挡住底下应用的点击。
  const stageHitTest = useCallback((x: number, y: number): boolean => {
    const probe3d = hit3DRef.current

    if (probe3d) {
      return probe3d(x, y) ?? true
    }

    const hitmap = $mesh2dHitmap.get()

    if (!hitmap) {
      return true
    }

    const rect = mountRef.current?.getBoundingClientRect()

    if (!rect || rect.width <= 0 || rect.height <= 0) {
      return false
    }

    return hitmap.hit((x - rect.left) / rect.width, (y - rect.top) / rect.height) !== null
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

  // 文件投喂（DESIGN §6.3）：解析真实文件路径并推到 chat-dock。
  // 抽成独立 async 函数让 onDrop handler 保持同步。
  const handleDrop = async (fileList: FileList | null | undefined): Promise<void> => {
    const files = Array.from(fileList ?? [])

    if (files.length === 0) {
      return
    }

    // Electron 32+ 移除了 File.path——必须经 webUtils.getPathForFile 拿真实路径。
    // 浏览器没有 webUtils 时回退到 dataURL（虽然下游路径基于 file://，浏览器几乎不会走到这里）。
    const webUtils = (window as unknown as { spiritagentWebUtils?: { getPathForFile: (f: File) => string } })
      .spiritagentWebUtils

    const paths: string[] = []

    for (const f of files) {
      if (webUtils) {
        try {
          const p = webUtils.getPathForFile(f)

          if (p) {
            paths.push(p)

            continue
          }
        } catch {
          /* 单个文件解析失败不影响其他文件 */
        }
      }

      // 主进程未暴露 webUtils（开发态 / 浏览器预览）——读 dataURL 作为退化
      if (f instanceof Blob) {
        try {
          const dataUrl = await new Promise<string>((resolve, reject) => {
            const reader = new FileReader()
            reader.onload = () => resolve(reader.result as string)
            reader.onerror = () => reject(reader.error)
            reader.readAsDataURL(f)
          })

          paths.push(dataUrl)
        } catch {
          /* ignore */
        }
      }
    }

    if (paths.length === 0) {
      return
    }

    // 接取动效（DESIGN §6.3「触发接取动效与爱心/音符反馈」）：抬手接住 + 爱心/音符粒子
    emitVfx('heart', { nx: 0.5, ny: 0.25, count: 3 })
    emitVfx('music_notes', { nx: 0.35, ny: 0.15, count: 3 })
    $clipOverride.set('present_right')
    $spriteAction.set('present_right')
    setSpriteState('interacting', { durationMs: 2000 })
    pushExternalAttachment(paths)
    // 投喂文件时自动打开聊天面板，让用户看到附件被加入；
    // 走根组件的 openDock 走 dock 互斥（不能直接 setChatOpen，否则会和
    // voice 通话面板同时弹出）。
    const openDock = (window as unknown as { __spiritagentOpenDock?: (k: 'chat') => void }).__spiritagentOpenDock
    openDock?.('chat')
  }

  const gestureTrackerRef = useRef<Mesh2DGestureTracker | null>(null)

  if (!gestureTrackerRef.current) {
    gestureTrackerRef.current = new Mesh2DGestureTracker({
      onPetStart: (nx, ny) => {
        handlePetInteraction(nx, ny)
      },
      onPetTick: (nx, ny) => {
        // 摸头粒子以爱心为主、花瓣偶尔混入（DESIGN §6.3 爱心 💖/🌸 同族）
        emitVfx(Math.random() < 0.35 ? 'petal' : 'heart', { nx, ny, count: 1 })
      },
      onShakeDizzy: () => {
        handleDizzyInteraction()
      }
    })
  }

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (hidden) {
      return
    }

    // 只在按下左键时捕获
    if (e.button !== 0) {
      return
    }

    if ($isEdgeDocked.get()) {
      undockFromEdge()
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
      lastTime: now,
      pressedAt: now
    }
    cancelMovement()

    // 启动独立 long-press 计时器：与 drag 完全解耦，drag 触发后会清掉。
    if (longPressTimerRef.current) {
      clearTimeout(longPressTimerRef.current)
    }

    longPressTimerRef.current = setTimeout(() => {
      longPressTimerRef.current = null
      const d = dragRef.current

      if (d && !d.moved) {
        // 触发时附带 VFX + sprite action：与拖拽的 drag_end 区分。
        // DESIGN §6.3 长按/拖拽与抛掷：「拖拽始终使用本地预制反馈」——长按是
        // 用户主动且未移动，可触发专属 sprite action 让其他模块响应。
        emitVfx('heart', { nx: 0.5, ny: 0.25, count: 2 })
        $spriteAction.set('long_press')
        setSpriteState('interacting', { durationMs: 800 })
      }
    }, LONG_PRESS_MS)
  }

  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    if (hidden) {
      return
    }

    const rect = mountRef.current?.getBoundingClientRect()
    const nx = rect && rect.width > 0 ? (e.clientX - rect.left) / rect.width : 0.5
    const ny = rect && rect.height > 0 ? (e.clientY - rect.top) / rect.height : 0.5
    const region = $mesh2dHitmap.get()?.hit(nx, ny)?.region

    const d = dragRef.current

    if (!d) {
      // DESIGN §3.2：贴边滑出要求命中可见部分——穿透态 forward 转发的 mousemove
      // 在矩形空白区也会到达这里，必须部件级命中判否，避免贴边时误触滑出。
      if ($isEdgeDocked.get() && stageHitTest(e.clientX, e.clientY)) {
        undockFromEdge()
      }

      gestureTrackerRef.current?.feedPointerMove(nx, ny, false, region)
      handleHoverInteraction(region)

      return
    }

    const dx = e.clientX - d.startX
    const dy = e.clientY - d.startY

    if (!d.moved && Math.hypot(dx, dy) > DRAG_THRESHOLD) {
      d.moved = true
      startDrag()
      e.currentTarget.setPointerCapture(e.pointerId)

      // drag 一旦开始就放弃 long-press 等待：与拖拽是互斥的两条交互通道。
      if (longPressTimerRef.current) {
        clearTimeout(longPressTimerRef.current)
        longPressTimerRef.current = null
      }
    }

    if (d.moved) {
      gestureTrackerRef.current?.feedPointerMove(nx, ny, true, region)

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

    if (longPressTimerRef.current) {
      clearTimeout(longPressTimerRef.current)
      longPressTimerRef.current = null
    }

    gestureTrackerRef.current?.feedPointerUp()
    ;(e.currentTarget as Element).releasePointerCapture?.(e.pointerId)
    const drag = dragRef.current
    dragRef.current = null
    lastDragPointRef.current = drag?.moved ? { x: drag.lastX, y: drag.lastY } : null

    if (dragRafRef.current !== null) {
      cancelAnimationFrame(dragRafRef.current)
      dragRafRef.current = null
    }

    const lastVel = pendingVelRef.current ?? undefined

    if (pendingPosRef.current) {
      updateDragPosition(pendingPosRef.current, lastVel)
      pendingPosRef.current = null
      pendingVelRef.current = null
    }

    if (drag?.moved) {
      endDragAt($spatialPos.get(), lastVel)
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
      // 计算归一化坐标 (nx, ny) 透传给 onTap，供 2D 路径子区域命中
      const rect = mountRef.current?.getBoundingClientRect()
      const nx = rect && rect.width > 0 ? (e.clientX - rect.left) / rect.width : 0.5
      const ny = rect && rect.height > 0 ? (e.clientY - rect.top) / rect.height : 0.5
      onTap?.(nx, ny)
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
        onDragOver={e => {
          e.preventDefault()
        }}
        onDrop={e => {
          e.preventDefault()
          void handleDrop(e.dataTransfer?.files)
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
        <Mesh2DVfxOverlay />
      </div>
    </div>
  )
}
