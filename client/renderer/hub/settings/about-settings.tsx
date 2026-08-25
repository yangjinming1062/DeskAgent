import { useStore } from '@nanostores/react'
import { useCallback, useEffect } from 'react'

import { $updateStatus, selectTargetVersion } from '@/hub/settings-store'
import { BrandMark } from '@/shared/components/brand-mark'
import { Button } from '@/shared/components/ui'
import { Loader2, RefreshCw } from '@/shared/lib/icons'
import { $desktopVersion, refreshDesktopVersion } from '@/shared/store/version'
import { strings } from '@/shared/strings'

import { SettingsContent } from './primitives'

export function AboutSettings(): React.JSX.Element {
  const t = strings
  const a = t.settings.about
  const version = useStore($desktopVersion)
  const updateStatus = useStore($updateStatus)

  useEffect(() => {
    void refreshDesktopVersion()
  }, [])

  const onCheckClick = useCallback(() => {
    // 触发手动检查。下方的状态栏徽标 + statusLine 都对 `$updateStatus` 做出响应，
    // 用户无需打开独立对话框即可看到状态切换。
    void window.spiritagent?.update?.check?.()
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
      <div className="flex flex-col items-center gap-3 pt-10 pb-2 text-center">
        <div className="relative grid place-items-center">
          {/* Static warm halo — echoes the companion's amber glow (egg.tsx).
              Settings is the admin surface, so no breathing; just identity warmth. */}
          <span
            aria-hidden="true"
            className="pointer-events-none absolute z-0 size-32 rounded-full"
            style={{
              background: 'radial-gradient(closest-side, rgba(255,209,102,0.28), transparent 70%)',
              filter: 'blur(12px)'
            }}
          />
          <BrandMark className="relative z-10 size-16" />
        </div>
        <div>
          <h2 className="text-lg font-semibold tracking-tight text-white">{a.heading}</h2>
          <p className="mt-1 text-xs text-white/40">
            {version?.appVersion ? a.version(version.appVersion) : a.versionUnavailable}
          </p>
        </div>
      </div>

      <div className="mx-auto flex w-full max-w-sm flex-col items-center gap-2 pt-4">
        <p aria-live="polite" className="text-center text-xs text-white/40">
          {statusLine}
        </p>
        <Button
          className="border-white/15 bg-white/5 text-white/80 hover:bg-white/15 hover:text-white"
          disabled={isChecking}
          onClick={onCheckClick}
          size="sm"
          variant="outline"
        >
          {isChecking ? <Loader2 className="animate-spin" /> : <RefreshCw />}
          {a.checkForUpdates}
        </Button>
      </div>
    </SettingsContent>
  )
}
