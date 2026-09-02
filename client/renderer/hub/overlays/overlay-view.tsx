import { type ReactNode, useEffect } from 'react'

import { triggerHaptic } from '@/shared/lib/haptics'
import type { IconComponent } from '@/shared/lib/icons'
import { HudCorners, PanelHeader } from '@/shared/panel'
import { strings } from '@/shared/strings'

interface OverlayViewProps {
  title: string
  icon?: IconComponent
  children: ReactNode
  onClose: () => void
  closeLabel?: string
}

// 工具窗页面外壳：与伙伴窗 FloatingPanel 同一款面板头（图标 + 标题 + 关闭钮，
// 头部即窗口拖拽区）+ Esc 关闭。
export function OverlayView({
  title,
  icon,
  children,
  onClose,
  closeLabel = strings.common.close
}: OverlayViewProps): React.JSX.Element {
  const handleClose = (): void => {
    triggerHaptic('close')
    onClose()
  }

  // Esc 关闭所有基于 OverlayView 的浮层。嵌套的 Radix 对话框会自行阻止冒泡，
  // 因此在 Settings 中打开（例如）模型选择器时，会先关闭选择器，
  // 而不是下层浮层。
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) {
        return
      }

      event.preventDefault()
      handleClose()
    }

    window.addEventListener('keydown', onKeyDown)

    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  return (
    <div className="fixed inset-0 flex min-h-0 flex-col overflow-hidden bg-surface-panel text-strong tech-grid">
      <HudCorners size={8} />
      <PanelHeader closeLabel={closeLabel} dragRegion icon={icon} onClose={handleClose} title={title} />
      <div className="min-h-0 flex flex-1 flex-col">{children}</div>
    </div>
  )
}
