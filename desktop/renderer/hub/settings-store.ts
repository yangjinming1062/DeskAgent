import { atom } from 'nanostores'

// Discriminated union — the main process forwards electron-updater events as
// `{ type, ... }` payloads on the `deskagent:update-event` IPC channel. Each
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

function selectTargetVersion(status: UpdateStatus): string {
  return 'version' in status && status.version ? status.version : ''
}

export { $updateStatus, selectTargetVersion, setUpdateStatus }
