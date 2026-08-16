import type { App, IpcMain } from 'electron'

export interface SystemIpcDeps {
  electron: { app: App }
  ipcMain: IpcMain
}

export function registerSystemIpc({ electron, ipcMain }: SystemIpcDeps): void {
  const { app } = electron

  ipcMain.handle('spiritagent:version', async () => ({
    appVersion: app.getVersion(),
    electronVersion: process.versions.electron,
    nodeVersion: process.versions.node,
    platform: process.platform
  }))
}
