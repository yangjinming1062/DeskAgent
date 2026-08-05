import { useStore } from '@nanostores/react'
import { useRef } from 'react'

import { $desktopBoot } from '@/companion/boot-store'
import { useInteractiveRegion } from '@/companion/interactive-regions'
import { Button } from '@/shared/components/ui/button'
import { ErrorState } from '@/shared/components/ui/error-state'
import { setPrimaryGateway } from '@/shared/store/gateway'
import { strings } from '@/shared/strings'

// Recovery surface for the boot-failure path; shown when failDesktopBoot flips the store to 'renderer.error'.
// Mirrors the ErrorBoundary full-screen pattern (RootErrorFallback) — same
// ErrorState + Button primitives, same z-index, same backdrop.
export function BootFailureOverlay() {
  const boot = useStore($desktopBoot)
  const overlayRef = useRef<HTMLDivElement>(null)
  const isError = boot.phase === 'renderer.error' && boot.error

  // Register the full-bleed overlay as an interactive region so the Retry
  // button stays clickable through the otherwise click-through sprite
  // window — without this, the window's setIgnoreMouseEvents(true, ...)
  // swallows every click in the failure state. The rect is a compile-time
  // constant (position: fixed; inset: 0) so we skip getBoundingClientRect
  // and return the viewport directly — interactive-regions calls this on
  // every mousemove across the screen while the failure state is up.
  useInteractiveRegion('boot-failure', overlayRef, () => new DOMRect(0, 0, window.innerWidth, window.innerHeight))

  if (!isError) {
    return null
  }

  const message = boot.message || strings.boot.errors.desktopBootFailed

  const onRetry = () => {
    // Reload re-runs the boot path, which only fires once per mount.
    setPrimaryGateway(null)
    window.location.reload()
  }

  return (
    <div
      aria-live="assertive"
      className="fixed inset-0 z-[1500] grid place-items-center bg-(--ui-chat-surface-background) p-6"
      ref={overlayRef}
      role="alertdialog"
    >
      <ErrorState className="w-full max-w-[28rem]" description={message} title={strings.boot.errors.desktopBootFailed}>
        <Button className="font-semibold" onClick={onRetry} size="lg">
          {strings.boot.failure.retry}
        </Button>
      </ErrorState>
    </div>
  )
}
