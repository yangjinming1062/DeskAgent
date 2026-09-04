import { useStore } from '@nanostores/react'
import { clamp } from '@runtime'
import type { ReactNode } from 'react'
import { useEffect, useRef } from 'react'

import { usePanelDrag } from '@/companion/hooks/use-panel-drag'
import { type ResizeDirection, usePanelResize } from '@/companion/hooks/use-panel-resize'
import { useInteractiveRegion } from '@/companion/interactive-regions'
import { centeredPanelPosition } from '@/companion/panel/panel-position'
import { $viewport } from '@/companion/spatial'
import type { IconComponent } from '@/shared/lib/icons'
import { BorderBeam, HudCorners, PanelHeader } from '@/shared/panel'

interface PanelSize {
  width: number
  height: number
}

interface FloatingPanelProps {
  regionId: string
  // localStorage 键前缀：尺寸 / 偏移分别落在 `${prefix}Size` / `${prefix}Offset`
  storagePrefix: string
  title: string
  icon?: IconComponent
  onClose: () => void
  defaultSize: PanelSize
  minSize?: PanelSize
  maxSize?: PanelSize
  children: ReactNode
  /** 关掉拖拽与缩放——固定 defaultSize 居中展示。用于统一设置面板等全屏模态场景。 */
  static?: boolean
}

export const RESIZE_HANDLES: Array<{ dir: ResizeDirection; className: string }> = [
  { dir: 'n', className: 'absolute -top-1 left-3 right-3 h-2.5 cursor-ns-resize z-20 touch-none' },
  { dir: 's', className: 'absolute -bottom-1 left-3 right-3 h-2.5 cursor-ns-resize z-20 touch-none' },
  { dir: 'w', className: 'absolute -left-1 top-3 bottom-3 w-2.5 cursor-ew-resize z-20 touch-none' },
  { dir: 'e', className: 'absolute -right-1 top-3 bottom-3 w-2.5 cursor-ew-resize z-20 touch-none' },
  { dir: 'nw', className: 'absolute -top-1.5 -left-1.5 h-4 w-4 cursor-nwse-resize z-30 touch-none' },
  { dir: 'ne', className: 'absolute -top-1.5 -right-1.5 h-4 w-4 cursor-nesw-resize z-30 touch-none' },
  { dir: 'sw', className: 'absolute -bottom-1.5 -left-1.5 h-4 w-4 cursor-nesw-resize z-30 touch-none' },
  { dir: 'se', className: 'absolute -bottom-1.5 -right-1.5 h-4 w-4 cursor-nwse-resize z-30 touch-none' }
]

// 伙伴窗大面板的通用外壳：交互区域登记 + 拖拽 + 八向缩放 + Esc 关闭 + 石墨实体表面。
// 穿透 wrapper 不绘制不捕获——只有面板本体（panelRef）参与命中。
export function FloatingPanel({
  regionId,
  storagePrefix,
  title,
  icon,
  onClose,
  defaultSize,
  minSize,
  maxSize,
  children,
  static: isStatic = false
}: FloatingPanelProps): React.JSX.Element {
  const viewport = useStore($viewport)
  const panelRef = useRef<HTMLDivElement>(null)

  useInteractiveRegion(regionId, panelRef)

  const { size: dynamicSize, getResizeHandleProps } = usePanelResize({
    sizeStorageKey: `${storagePrefix}Size`,
    offsetStorageKey: `${storagePrefix}Offset`,
    defaultSize,
    minSize,
    maxSize,
    getPanel: () => panelRef.current
  })

  const { bind: dragBind, storedOffset } = usePanelDrag(`${storagePrefix}Offset`, () => panelRef.current)

  // 静态模式：固定 defaultSize 居中展示，无拖拽与缩放。
  const size = isStatic ? defaultSize : dynamicSize
  const effectiveOffset = isStatic ? null : storedOffset

  // 面板几何上云（companion.settings_panel）：拖拽/缩放停稳后防抖上报，
  // 跳过挂载首跑——未交互过不上报，避免本机默认值覆写另一端的已存几何。
  const dxRaw = effectiveOffset?.dx ?? 0
  const dyRaw = effectiveOffset?.dy ?? 0
  const geometryReported = useRef(false)

  useEffect(() => {
    if (isStatic) {
      return
    }

    if (!geometryReported.current) {
      geometryReported.current = true

      return
    }

    const timer = window.setTimeout(() => {
      window.spiritagent?.prefs?.set({
        key: 'companion.settings_panel',
        value: { height: size.height, offsetX: dxRaw, offsetY: dyRaw, width: size.width }
      })
    }, 600)

    return () => window.clearTimeout(timer)
  }, [size, dxRaw, dyRaw, isStatic])

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key !== 'Escape' || e.defaultPrevented) {
        return
      }

      // 正在输入框里按 Esc（清空 / 取消编辑的惯例）不连带关掉整个面板——
      // 衣柜设计会话等组件内状态会随之丢失。
      const target = e.target as HTMLElement | null

      const isTyping =
        target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)

      if (isTyping) {
        return
      }

      e.preventDefault()
      onClose()
    }

    window.addEventListener('keydown', onKey)

    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const { left, top } = centeredPanelPosition(viewport, size)

  // 历史拖拽偏移可能把居中面板整个推出视口（只余一角）——渲染期钳制，
  // 保证面板在屏幕内至少保留 160px 宽 / 64px 高的可见条带。
  const dx = effectiveOffset?.dx ?? 0
  const dy = effectiveOffset?.dy ?? 0
  const visibleDx = clamp(left + dx, 176 - size.width, viewport.width - 160) - left
  const visibleDy = clamp(top + dy, 80 - size.height, viewport.height - 64) - top

  return (
    <div className="pointer-events-none fixed inset-0 z-50">
      <div
        className="relative flex flex-col overflow-hidden rounded-2xl border border-line-standard bg-surface-panel text-strong shadow-2xl border-beam-container"
        ref={panelRef}
        style={{
          position: 'fixed',
          left,
          top,
          width: `min(calc(100vw - 2rem), ${size.width}px)`,
          height: `min(calc(100vh - 2rem), ${size.height}px)`,
          pointerEvents: 'auto',
          transform: `translate3d(${visibleDx}px, ${visibleDy}px, 0)`
        }}
      >
        <BorderBeam />
        <HudCorners size={8} />
        {!isStatic &&
          RESIZE_HANDLES.map(h => (
            <div aria-hidden="true" className={h.className} key={h.dir} {...getResizeHandleProps(h.dir)} />
          ))}
        <PanelHeader dragBind={isStatic ? undefined : dragBind} icon={icon} onClose={onClose} title={title} />
        <div className="flex min-h-0 flex-1 flex-col">{children}</div>
      </div>
    </div>
  )
}
