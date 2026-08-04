import { useStore } from '@nanostores/react'

import { $desktopBoot } from '@/companion/boot-store'
import { setPrimaryGateway } from '@/shared/store/gateway'
import { strings } from '@/shared/strings'

// Recovery surface for the boot-failure path; shown when failDesktopBoot flips the store to 'renderer.error'.
export function BootFailureOverlay() {
  const boot = useStore($desktopBoot)

  if (boot.phase !== 'renderer.error' || !boot.error) {
    return null
  }

  const message = boot.message || strings.boot.errors.desktopBootFailed

  return (
    <div aria-live="assertive" className="boot-failure-overlay" role="alertdialog">
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
