import { type ReactNode, useEffect } from 'react'

import { Button, Codicon } from '@/shared/components/ui'
import { triggerHaptic } from '@/shared/lib/haptics'
import { strings } from '@/shared/strings'

// Win/Linux draw the native WindowControlsOverlay at the top-right; an in-app
// close button there would sit underneath it. macOS traffic lights live at
// the top-left, so the in-app close survives.
const HAS_NATIVE_WINDOW_CONTROLS = !navigator.userAgent.includes('Mac')

interface OverlayViewProps {
  children: ReactNode
  onClose: () => void
  closeLabel?: string
}

// Full-bleed page shell for the framed tool window: a drag band below the
// native window controls, plus Esc-to-close.
export function OverlayView({
  children,
  onClose,
  closeLabel = strings.common.close
}: OverlayViewProps): React.JSX.Element {
  const closeOverlay = () => {
    triggerHaptic('close')
    onClose()
  }

  // Esc dismisses every OverlayView-based overlay. Nested Radix dialogs
  // stop propagation themselves, so opening (e.g.) the model picker inside
  // Settings still closes the picker first instead of the underlying overlay.
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
