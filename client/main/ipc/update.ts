import type { App, BrowserWindow, IpcMain } from 'electron'

export interface UpdateIpcDeps {
  electron: { app: App }
  getMainWindow: () => BrowserWindow | null | undefined
  ipcMain: IpcMain
  sendToMain: (win: BrowserWindow | null | undefined, channel: string, payload: any) => void
}

export function registerUpdateIpc({ electron, getMainWindow, ipcMain, sendToMain }: UpdateIpcDeps): void {
  const { app } = electron

  if (!app.isPackaged) {
    return
  }

  const log = require('electron-log/main')
  const { autoUpdater } = require('electron-updater')
  autoUpdater.logger = log

  function broadcast(eventName: string, payload: Record<string, any> = {}): void {
    const win = getMainWindow()
    sendToMain(win, 'deskagent:update-event', { type: eventName, ...payload })
  }

  ipcMain.handle('deskagent:update:check', async () => {
    try {
      await autoUpdater.checkForUpdates()
    } catch (e: any) {
      broadcast('error', { message: String(e?.message || e) })
    }
  })

  autoUpdater.on('checking-for-update', () => broadcast('checking'))
  autoUpdater.on('update-available', (info: any) => broadcast('available', { info }))
  autoUpdater.on('update-not-available', (info: any) => broadcast('none', { info }))
  autoUpdater.on('download-progress', (progress: any) => broadcast('progress', { progress }))
  autoUpdater.on('update-downloaded', (info: any) => broadcast('downloaded', { info }))
  autoUpdater.on('error', (err: any) => {
    const message = String(err?.message || err)

    if (message.includes('404') && message.includes('latest.yml')) {
      broadcast('none', { info: null })
    } else {
      broadcast('error', { message })
    }
  })
}
