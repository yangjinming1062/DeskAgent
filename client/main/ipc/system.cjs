'use strict'

// IPC channel for app-level system info (version). No shared state — pulls
// everything from `electron` directly.
function registerSystemIpc({ ipcMain, electron }) {
  const { app } = electron

  ipcMain.handle('deskagent:version', async () => ({
    appVersion: app.getVersion(),
    electronVersion: process.versions.electron,
    nodeVersion: process.versions.node,
    platform: process.platform
  }))
}

module.exports = { registerSystemIpc }
