'use strict'

// IPC channels for app-level system info, native notifications, and macOS mic
// permission. No shared state — pulls everything from `electron` directly.
function registerSystemIpc({ ipcMain, electron }) {
  const { app, Notification, systemPreferences } = electron

  ipcMain.handle('deskagent:requestMicrophoneAccess', async () => {
    if (process.platform !== 'darwin' || typeof systemPreferences.askForMediaAccess !== 'function') {
      return true
    }

    return systemPreferences.askForMediaAccess('microphone')
  })

  ipcMain.handle('deskagent:notify', (_event, payload) => {
    if (!Notification.isSupported()) return false
    new Notification({
      title: payload?.title || 'DeskAgent',
      body: payload?.body || '',
      silent: Boolean(payload?.silent)
    }).show()
    return true
  })

  ipcMain.handle('deskagent:version', async () => ({
    appVersion: app.getVersion(),
    electronVersion: process.versions.electron,
    nodeVersion: process.versions.node,
    platform: process.platform
  }))
}

module.exports = { registerSystemIpc }
