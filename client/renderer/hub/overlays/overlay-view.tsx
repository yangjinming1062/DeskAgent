import { type ReactNode, useEffect } from 'react'

import { Button, Codicon } from '@/shared/components/ui'
import { triggerHaptic } from '@/shared/lib/haptics'
import { strings } from '@/shared/strings'

// Win / Linux 把原生 WindowControlsOverlay 画在右上角；那里的应用内
// 关闭按钮会被盖在下面。macOS 的红绿灯在左上角，
// 因此应用内关闭按钮可以保留。
const HAS_NATIVE_WINDOW_CONTROLS = !navigator.userAgent.includes('Mac')

interface OverlayViewProps {
  children: ReactNode
  onClose: () => void
  closeLabel?: string
}

// 工具窗口的全出血页面外壳：原生窗口控件下方的拖拽带 + Esc 关闭。
export function OverlayView({
  children,
  onClose,
  closeLabel = strings.common.close
}: OverlayViewProps): React.JSX.Element {
  const closeOverlay = () => {
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
      triggerHaptic('close')
      onClose()
    }

    window.addEventListener('keydown', onKeyDown)

    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  return (
    <div className="fixed inset-0 flex min-h-0 flex-col overflow-hidden bg-(--ui-chat-surface-background)">
      <div className="pointer-events-none absolute inset-x-0 top-0 z-10 h-[calc(var(--titlebar-height)+0.1875rem)] [-webkit-app-region:drag]">
        {!HAS_NATIVE_WINDOW_CONTROLS && (
          <Button
            aria-label={closeLabel}
            className="pointer-events-auto absolute right-3 top-[calc(0.1875rem+var(--titlebar-height)/2)] -translate-y-1/2 text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground [-webkit-app-region:no-drag]"
            onClick={closeOverlay}
            size="icon-titlebar"
            variant="ghost"
          >
            <Codicon name="close" size="1rem" />
          </Button>
        )}
      </div>

      {/* No top padding here: the split-layout columns own their own
          titlebar clearance so their backgrounds run flush to the page top. */}
      <div className="min-h-0 flex flex-1 flex-col">{children}</div>
    </div>
  )
}
