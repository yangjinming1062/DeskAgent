import type { IpcMain } from 'electron'

export function formatRendererLog(payload: { args?: unknown[]; level?: string; scope?: string }): string {
  const { args, scope } = payload ?? {}

  const parts = (Array.isArray(args) ? args : [args]).map(a => {
    if (a == null) {
      return String(a)
    }

    if (typeof a === 'object' && (a as any).message) {
      return (a as any).message
    }

    if (typeof a === 'object') {
      try {
        return JSON.stringify(a)
      } catch {
        return String(a)
      }
    }

    return String(a)
  })

  return `[renderer:${scope}] ${parts.join(' ')}`
}

export function registerLogIpc({ ipcMain, log }: { ipcMain: IpcMain; log: (msg: string) => void }): void {
  ipcMain.handle('deskagent:log:emit', (_event, payload) => {
    log(formatRendererLog(payload))
  })
}
