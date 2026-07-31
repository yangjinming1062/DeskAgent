import { useStore } from '@nanostores/react'
import { useCallback, useEffect } from 'react'

import { $updateStatus, openUpdateDialog, selectTargetVersion } from '@/hub/settings-store'
import { BrandMark } from '@/shared/components/brand-mark'
import { Button } from '@/shared/components/ui/button'
import { Loader2, RefreshCw } from '@/shared/lib/icons'
import { $desktopVersion, refreshDesktopVersion } from '@/shared/store/version'
import { strings } from '@/shared/strings'

import { SettingsContent } from './primitives'

export function AboutSettings() {
  const t = strings
  const a = t.settings.about
  const version = useStore($desktopVersion)
  const updateStatus = useStore($updateStatus)

  useEffect(() => {
    void refreshDesktopVersion()
  }, [])

  const onCheckClick = useCallback(() => {
    // Open the dialog immediately so the user sees the transition, then
    // kick off the manual check. The dialog's progress copy reacts to the
    // `$updateStatus` atom — even if the check resolves to "none" the
    // dialog reports it inline.
    openUpdateDialog()
    void window.deskagent?.update?.check?.()
  }, [])

  const isChecking = updateStatus.status === 'checking'
  const isAvailable = updateStatus.status === 'available' || updateStatus.status === 'downloading'
  const isDownloaded = updateStatus.status === 'downloaded'

  let statusLine: string = a.upToDate

  if (isChecking) {
    statusLine = a.checking
  } else if (isDownloaded) {
    statusLine = a.updateDownloaded(selectTargetVersion(updateStatus))
  } else if (isAvailable) {
    statusLine = a.updateAvailable(selectTargetVersion(updateStatus))
  } else if (updateStatus.status === 'none' && version?.appVersion) {
    statusLine = a.upToDateWithVersion(version.appVersion)
  } else if (updateStatus.status === 'error') {
    statusLine = a.updateError(updateStatus.message)
  }

  return (
    <SettingsContent>
      <div className="flex flex-col items-center gap-3 pt-6 pb-2 text-center">
        <BrandMark className="size-16" />
        <div>
          <h2 className="text-lg font-semibold tracking-tight">{a.heading}</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            {version?.appVersion ? a.version(version.appVersion) : a.versionUnavailable}
          </p>
        </div>
      </div>

      <div className="mx-auto flex w-full max-w-sm flex-col items-center gap-2 pt-4">
        <p aria-live="polite" className="text-center text-xs text-muted-foreground">
          {statusLine}
        </p>
        <Button disabled={isChecking} onClick={onCheckClick} size="sm" variant="outline">
          {isChecking ? <Loader2 className="animate-spin" /> : <RefreshCw />}
          {a.checkForUpdates}
        </Button>
      </div>
    </SettingsContent>
  )
}
