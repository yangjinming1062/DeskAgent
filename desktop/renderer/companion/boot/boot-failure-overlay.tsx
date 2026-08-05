import { useStore } from '@nanostores/react'
import { useEffect, useRef } from 'react'

import { $desktopBoot } from '@/companion/boot-store'
import { registerInteractiveRegion, unregisterInteractiveRegion } from '@/companion/interactive-regions'
import { setPrimaryGateway } from '@/shared/store/gateway'
import { strings } from '@/shared/strings'

// Recovery surface for the boot-failure path; shown when failDesktopBoot flips the store to 'renderer.error'.
export function BootFailureOverlay() {
  const boot = useStore($desktopBoot)
  const overlayRef = useRef<HTMLDivElement>(null)
  const isError = boot.phase === 'renderer.error' && boot.error

  // Register the full-bleed overlay as an interactive region so the Retry
  // button stays clickable through the otherwise click-through sprite
  // window — without this, the window's setIgnoreMouseEvents(true, ...)
  // swallows every click in the failure state. The rect is a compile-time
  // constant (position: fixed; inset: 0) so skip getBoundingClientRect and
  // return the viewport directly — interactive-regions calls this on every
  // mousemove across the screen while the failure state is up.
  useEffect(() => {
    if (!isError) {
      return
    }

    registerInteractiveRegion('boot-failure', () => new DOMRect(0, 0, window.innerWidth, window.innerHeight))

    return () => unregisterInteractiveRegion('boot-failure')
  }, [isError])

  if (!isError) {
    return null
  }

  const message = boot.message || strings.boot.errors.desktopBootFailed

  return (
    <div aria-live="assertive" className="boot-failure-overlay" ref={overlayRef} role="alertdialog">
      <div className="boot-failure-card">
        <h2>{strings.boot.errors.desktopBootFailed}</h2>
        <p className="boot-failure-message">{message}</p>
        <div className="boot-failure-actions">
          <button
            className="boot-failure-retry"
            onClick={() => {
              // Reload re-runs the boot path, which only fires once per mount.
              setPrimaryGateway(null)
              window.location.reload()
            }}
            type="button"
          >
            {strings.boot.failure.retry}
          </button>
        </div>
      </div>
    </div>
  )
}
