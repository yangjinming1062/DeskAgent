'use strict'

function registerUpdateIpc({ ipcMain, electron, sendToMain, getMainWindow }) {
  const { app } = electron

  // In dev, electron-updater throws when the asar doesn't carry an
  // app-update.yml — bail before requiring it.
  if (!app.isPackaged) {
    return
  }

  const { autoUpdater } = require('electron-updater')
  const log = require('electron-log/main')
  autoUpdater.logger = log

  ipcMain.handle('deskagent:update:check', async () => {
    try {
      await autoUpdater.checkForUpdates()
    } catch (e) {
      broadcast('error', { message: String(e?.message || e) })
    }
  })

  // Local helper to forward autoUpdater events. Called from main.cjs'
  // setupAutoUpdater; lives here so the IPC module owns its full surface.
  function broadcast(eventName, payload) {
    const win = getMainWindow()
    sendToMain(win, 'deskagent:update-event', { type: eventName, ...payload })
  }

  autoUpdater.on('checking-for-update', () => broadcast('checking'))
  autoUpdater.on('update-available', info => broadcast('available', { info }))
  autoUpdater.on('update-not-available', info => broadcast('none', { info }))
  autoUpdater.on('download-progress', progress => broadcast('progress', { progress }))
  autoUpdater.on('update-downloaded', info => broadcast('downloaded', { info }))
  autoUpdater.on('error', err => {
    const message = String(err?.message || err)
    if (message.includes('404') && message.includes('latest.yml')) {
      broadcast('none', { info: null })
    } else {
      broadcast('error', { message })
    }
  })
}

module.exports = { registerUpdateIpc }
