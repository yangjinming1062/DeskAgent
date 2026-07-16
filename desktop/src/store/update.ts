import { atom } from 'nanostores'

// Discriminated union — the main process forwards electron-updater events as
// `{ type, ... }` payloads on the `zast:update-event` IPC channel. Each
// variant mirrors one autoUpdater.on(...) callback.
export type UpdateStatus =
  | { status: 'idle' }
  | { status: 'checking' }
  | {
      status: 'available'
      version: string
      releaseDate?: string
    }
  | { status: 'none'; version?: string }
  | {
      status: 'downloading'
      percent: number
      transferred: number
      total: number
    }
  | { status: 'downloaded'; version: string }
  | { status: 'error'; message: string }

const $updateStatus = atom<UpdateStatus>({ status: 'idle' })

function setUpdateStatus(next: UpdateStatus) {
  $updateStatus.set(next)
}

// The update dialog (banner + Restart/Later buttons). Mounted once at the app
// root; opened by the status-bar badge or the About-panel "Check for
// updates" button. Idempotent: re-opening with the dialog already visible
// is a no-op so the auto-downloaded event can re-fire without stacking.
const $updateDialogOpen = atom<boolean>(false)

function openUpdateDialog() {
  $updateDialogOpen.set(true)
}

function closeUpdateDialog() {
  $updateDialogOpen.set(false)
}

// Runner-side update state. The desktop auto-updates the inner Electron
// binary, then in the same flow prefetches the Python runner wheel +
// server.py to $ZAST_HOME/runner.staging/ (Phase 1, in the OLD Electron),
// and on next launch `pip install --upgrade` the wheel into the existing
// venv and overwrites server.py (Phase 2, in the NEW Electron). The toast
// renders this state to keep the user informed through the full lifecycle.
export type RunnerUpdateStatus =
  | { status: 'idle' }
  | { status: 'prefetching'; version: string; phase: 'manifest' | 'wheel' | 'server'; percent?: number }
  | { status: 'ready'; version: string }
  | { status: 'installing'; version: string; phase: 'pip' | 'starting'; percent?: number }
  | { status: 'installed'; version: string }
  | { status: 'failed'; error: string; recoverable: boolean; version?: string }

const $runnerUpdateStatus = atom<RunnerUpdateStatus>({ status: 'idle' })

function setRunnerUpdateStatus(next: RunnerUpdateStatus) {
  $runnerUpdateStatus.set(next)
}

function selectTargetVersion(status: UpdateStatus): string {
  return 'version' in status && status.version ? status.version : ''
}

export {
  $runnerUpdateStatus,
  $updateDialogOpen,
  $updateStatus,
  closeUpdateDialog,
  openUpdateDialog,
  selectTargetVersion,
  setRunnerUpdateStatus,
  setUpdateStatus
}
