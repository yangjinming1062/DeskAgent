import { useStore } from '@nanostores/react'
import type { ReactNode } from 'react'
import { useEffect, useRef } from 'react'

import { usePanelDrag } from '@/companion/hooks/use-panel-drag'
import { type ResizeDirection, usePanelResize } from '@/companion/hooks/use-panel-resize'
import { useInteractiveRegion } from '@/companion/interactive-regions'
import { $viewport } from '@/companion/spatial'
import type { IconComponent } from '@/shared/lib/icons'
import { PanelHeader } from '@/shared/panel'

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
  children
}: FloatingPanelProps): React.JSX.Element {
  const viewport = useStore($viewport)
  const panelRef = useRef<HTMLDivElement>(null)

  useInteractiveRegion(regionId, panelRef)

  const { size, getResizeHandleProps } = usePanelResize({
    sizeStorageKey: `${storagePrefix}Size`,
    offsetStorageKey: `${storagePrefix}Offset`,
    defaultSize,
    minSize,
    maxSize,
    getPanel: () => panelRef.current
  })

  const { bind: dragBind, storedOffset } = usePanelDrag(`${storagePrefix}Offset`, () => panelRef.current)

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key !== 'Escape' || e.defaultPrevented) {
        return
      }

      // 正在输入框里按 Esc（清空 / 取消编辑的惯例）不连带关掉整个面板——
      // 衣柜设计会话等组件内状态会随之丢失。
      const target = e.target

      if (
        target instanceof HTMLElement &&
        (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)
      ) {
        return
      }

      e.preventDefault()
      onClose()
    }

    window.addEventListener('keydown', onKey)

    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const left = Math.max(16, Math.round((viewport.width - Math.min(viewport.width - 32, size.width)) / 2))
  const top = Math.max(16, Math.round((viewport.height - Math.min(viewport.height - 32, size.height)) / 2))

  // 历史拖拽偏移可能把居中面板整个推出视口（只余一角）——渲染期钳制，
  // 保证面板在屏幕内至少保留 160px 宽 / 64px 高的可见条带。
  const dx = storedOffset?.dx ?? 0
  const dy = storedOffset?.dy ?? 0
  const visibleDx = Math.min(Math.max(left + dx, 176 - size.width), viewport.width - 160) - left
  const visibleDy = Math.min(Math.max(top + dy, 80 - size.height), viewport.height - 64) - top

  return (
    <div className="pointer-events-none fixed inset-0 z-50">
      <div
        className="relative flex flex-col overflow-hidden rounded-2xl border border-white/12 bg-surface-panel text-white shadow-2xl"
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
        {RESIZE_HANDLES.map(h => (
          <div aria-hidden="true" className={h.className} key={h.dir} {...getResizeHandleProps(h.dir)} />
        ))}
        <PanelHeader dragBind={dragBind} icon={icon} onClose={onClose} title={title} />
        <div className="flex min-h-0 flex-1 flex-col">{children}</div>
      </div>
    </div>
  )
}
