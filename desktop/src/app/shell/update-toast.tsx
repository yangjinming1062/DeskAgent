import { useStore } from '@nanostores/react'
import { useCallback, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { useI18n } from '@/i18n'
import { Download, Loader2, RefreshCw } from '@/lib/icons'
import {
  $runnerUpdateStatus,
  $updateDialogOpen,
  $updateStatus,
  closeUpdateDialog,
  selectTargetVersion,
  setUpdateStatus
} from '@/store/update'

// Mounted once at the app root (next to <BootFailureOverlay />). The dialog
// opens from two entry points: the status-bar version badge and the
// Settings → About "Check for updates" button. The dialog's body reacts to
// the `$updateStatus` atom — same source the status bar reads — so the two
// stay in lock-step.
export function UpdateToast() {
  const { t } = useI18n()
  const a = t.settings.about
  const open = useStore($updateDialogOpen)
  const status = useStore($updateStatus)
  const runnerStatus = useStore($runnerUpdateStatus)
  const [busy, setBusy] = useState<'download' | 'install' | 'runner' | null>(null)

  const onDownload = useCallback(async () => {
    setBusy('download')

    try {
      const result = await window.zastDesktop?.update?.download?.()

      if (result && !result.ok) {
        setUpdateStatus({ status: 'error', message: result.reason ?? 'download failed' })
      }
    } finally {
      setBusy(null)
    }
  }, [])

  const onInstall = useCallback(async () => {
    setBusy('install')

    try {
      // The main process calls autoUpdater.quitAndInstall() which exits the
      // app; the IPC return value never lands in the renderer.
      await window.zastDesktop?.update?.install?.()
    } catch {
      // ignore — process is exiting.
    } finally {
      setBusy(null)
    }
  }, [])

  const onClose = useCallback(() => {
    closeUpdateDialog()
  }, [])

  const onRetryRunner = useCallback(async () => {
    setBusy('runner')

    try {
      await window.zastDesktop?.update?.retryRunnerInstall?.()
    } finally {
      setBusy(null)
    }
  }, [])

  // Status-driven body content. The dialog stays open through every state
  // so the user sees the full lifecycle of "checking → available →
  // downloading → ready → install".
  const targetVersion = selectTargetVersion(status)

  const body = (() => {
    if (status.status === 'idle') {
      return <p className="text-sm text-(--ui-text-tertiary)">{a.upToDate}</p>
    }

    if (status.status === 'checking') {
      return (
        <div className="flex items-center gap-2 text-sm text-(--ui-text-tertiary)">
          <Loader2 className="size-4 animate-spin" />
          {a.checking}
        </div>
      )
    }

    if (status.status === 'none') {
      return <p className="text-sm text-(--ui-text-tertiary)">{a.upToDate}</p>
    }

    if (status.status === 'available') {
      return (
        <>
          <p className="text-sm text-(--ui-text-tertiary)">{a.updateAvailable(targetVersion)}</p>
          <DialogFooter>
            <Button onClick={onClose} size="sm" variant="ghost">
              {a.later}
            </Button>
            <Button disabled={busy !== null} onClick={onDownload} size="sm">
              {busy === 'download' ? <Loader2 className="animate-spin" /> : <Download />}
              {a.download}
            </Button>
          </DialogFooter>
        </>
      )
    }

    if (status.status === 'downloading') {
      const percent = Math.max(0, Math.min(100, status.percent ?? 0))

      return (
        <>
          <p className="text-sm text-(--ui-text-tertiary)">{a.updateAvailable(targetVersion)}</p>
          <div className="flex flex-col gap-1">
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-(--ui-text-tertiary)/20">
              <div
                aria-label={`${Math.round(percent)}%`}
                className="h-full rounded-full bg-primary transition-[width] duration-200"
                style={{ width: `${percent}%` }}
              />
            </div>
            <p className="text-xs text-(--ui-text-tertiary)">{Math.round(percent)}%</p>
          </div>
        </>
      )
    }

    if (status.status === 'downloaded') {
      return (
        <>
          <p className="text-sm text-(--ui-text-tertiary)">{a.updateDownloaded(targetVersion)}</p>
          <DialogFooter>
            <Button onClick={onClose} size="sm" variant="ghost">
              {a.later}
            </Button>
            <Button disabled={busy !== null} onClick={onInstall} size="sm">
              {busy === 'install' ? <Loader2 className="animate-spin" /> : <RefreshCw />}
              {a.restart}
            </Button>
          </DialogFooter>
        </>
      )
    }

    if (status.status === 'error') {
      return <p className="text-sm text-destructive">{a.updateError(status.message)}</p>
    }

    return null
  })()

  // Runner-side status line. Only shown when phase 1 (prefetch in the OLD
  // Electron) is in flight or when phase 2 (install in the NEW Electron)
  // has not yet finished. The dialog stays open across the whole lifecycle
  // — desktop swap + runner install — so the user sees the full sequence.
  const runnerBody = (() => {
    if (runnerStatus.status === 'idle') {
      return null
    }

    if (runnerStatus.status === 'ready') {
      return (
        <p className="mt-3 text-xs text-(--ui-text-tertiary)">
          Runner v{runnerStatus.version} ready — restart to install.
        </p>
      )
    }

    if (runnerStatus.status === 'prefetching') {
      const label =
        runnerStatus.phase === 'manifest'
          ? 'Fetching manifest…'
          : runnerStatus.phase === 'wheel'
            ? `Downloading wheel… ${runnerStatus.percent ?? 0}%`
            : `Downloading server.py… ${runnerStatus.percent ?? 0}%`

      return (
        <p className="mt-3 flex items-center gap-2 text-xs text-(--ui-text-tertiary)">
          <Loader2 className="size-3 animate-spin" />
          {label}
        </p>
      )
    }

    if (runnerStatus.status === 'installing') {
      const label = runnerStatus.phase === 'pip' ? `Upgrading runner wheel (pip)…` : 'Starting new runner…'

      return (
        <p className="mt-3 flex items-center gap-2 text-xs text-(--ui-text-tertiary)">
          <Loader2 className="size-3 animate-spin" />
          {label}
        </p>
      )
    }

    if (runnerStatus.status === 'installed') {
      return (
        <p className="mt-3 text-xs text-(--ui-success, oklch(0.7 0.18 145))">
          ✓ Runner v{runnerStatus.version} installed.
        </p>
      )
    }

    if (runnerStatus.status === 'failed') {
      return (
        <div className="mt-3 flex items-center justify-between gap-2 rounded-md border border-(--ui-error) bg-(--ui-error)/5 p-2 text-xs">
          <span className="text-(--ui-error)">
            Runner update failed{runnerStatus.recoverable ? '' : ' — please reinstall'}.
          </span>
          {runnerStatus.recoverable && (
            <Button disabled={busy !== null} onClick={onRetryRunner} size="sm" variant="ghost">
              {busy === 'runner' ? <Loader2 className="animate-spin" /> : <RefreshCw />}
              Retry
            </Button>
          )}
        </div>
      )
    }

    return null
  })()

  return (
    <Dialog
      onOpenChange={next => {
        if (next) {
          $updateDialogOpen.set(true)
        } else {
          closeUpdateDialog()
        }
      }}
      open={open}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle icon={RefreshCw}>{a.checkForUpdates}</DialogTitle>
          <DialogDescription>{a.version('v' + (targetVersion || '—'))}</DialogDescription>
        </DialogHeader>
        {body}
        {runnerBody}
      </DialogContent>
    </Dialog>
  )
}
