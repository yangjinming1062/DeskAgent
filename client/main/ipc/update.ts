import type { App, BrowserWindow, IpcMain } from 'electron'

import type { DesktopUpdateEvent, DesktopUpdateInfo, DesktopUpdateProgress } from '../shared/ipc-contracts'

export interface UpdateIpcDeps {
  electron: { app: App }
  getMainWindow: () => BrowserWindow | null | undefined
  ipcMain: IpcMain
  sendToMain: (win: BrowserWindow | null | undefined, channel: string, payload: DesktopUpdateEvent) => void
}

export function registerUpdateIpc({ electron, getMainWindow, ipcMain, sendToMain }: UpdateIpcDeps): void {
  const { app } = electron

  if (!app.isPackaged) {
    return
  }

  const log = require('electron-log/main')
  const { autoUpdater } = require('electron-updater')
  autoUpdater.logger = log

  function broadcast(event: DesktopUpdateEvent): void {
    const win = getMainWindow()
    sendToMain(win, 'deskagent:update-event', event)
  }

  ipcMain.handle('deskagent:update:check', async () => {
    try {
      await autoUpdater.checkForUpdates()
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      broadcast({ message: msg, type: 'error' })
    }
  })

  autoUpdater.on('checking-for-update', () => broadcast({ type: 'checking' }))
  autoUpdater.on('update-available', (info: DesktopUpdateInfo) => broadcast({ info, type: 'available' }))
  autoUpdater.on('update-not-available', (info: DesktopUpdateInfo) => broadcast({ info, type: 'none' }))
  autoUpdater.on('download-progress', (progress: DesktopUpdateProgress) => broadcast({ progress, type: 'progress' }))
  autoUpdater.on('update-downloaded', (info: DesktopUpdateInfo) => broadcast({ info, type: 'downloaded' }))
  autoUpdater.on('error', (err: unknown) => {
    const message = err instanceof Error ? err.message : String(err)

    if (message.includes('404') && message.includes('latest.yml')) {
      broadcast({ info: undefined, type: 'none' })
    } else {
      broadcast({ message, type: 'error' })
    }
  })
}
