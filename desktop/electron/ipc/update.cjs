'use strict'

// IPC handlers for the inner-desktop auto-updater. The flow is:
//   1. main process (main.cjs) auto-checks for updates 30s after launch.
//   2. autoUpdater events are forwarded to the renderer as `deskagent:update-event`.
//   3. the renderer drives the user-visible flow (status bar badge, toast)
//      and calls these handlers for the explicit user actions:
//        - check (manual "Check for updates" button)
//        - download (start fetching the available update)
//        - install (commit the staged update and relaunch)
//
// In dev mode the handlers short-circuit to no-ops. The `electron-updater`
// require below is deferred to inside the function because destructuring
// `autoUpdater` at module load eagerly constructs AppUpdater, which crashes
// when the asar lacks `app-update.yml` (the dev case).

function registerUpdateIpc({ ipcMain, electron, sendToMain, getMainWindow, runnerUpdater }) {
  const { app } = electron

  // In dev, electron-updater throws when the asar doesn't carry an
  // app-update.yml. We expose the no-op path so renderer code can call
  // these handlers uniformly without feature-flagging on the renderer side.
  if (!app.isPackaged) {
    for (const channel of [
      'deskagent:update:check',
      'deskagent:update:download',
      'deskagent:update:install',
      'deskagent:update:status',
      'deskagent:update:runner:install'
    ]) {
      ipcMain.handle(channel, () => ({ ok: false, reason: 'dev-mode' }))
    }
    return
  }

  const { autoUpdater } = require('electron-updater')
  const log = require('electron-log/main')
  autoUpdater.logger = log

  ipcMain.handle('deskagent:update:check', async () => {
    try {
      await autoUpdater.checkForUpdates()
      return { ok: true }
    } catch (error) {
      const message = String(error?.message || error)
      if (message.includes('404') && message.includes('latest.yml')) {
        return { ok: true }
      }
      return { ok: false, reason: message }
    }
  })

  ipcMain.handle('deskagent:update:download', async () => {
    try {
      await autoUpdater.downloadUpdate()
      return { ok: true }
    } catch (error) {
      return { ok: false, reason: String(error?.message || error) }
    }
  })

  // quitAndInstall is fire-and-forget; the app will exit before we can
  // send a response. Renderer schedules this only on explicit user
  // confirmation ("Restart now"), never on app quit.
  ipcMain.handle('deskagent:update:install', () => {
    autoUpdater.quitAndInstall(false, false)
    return { ok: true }
  })

  ipcMain.handle('deskagent:update:status', () => ({
    currentVersion: app.getVersion()
  }))

  // Renderer retry of phase 2 install after a previous attempt failed and
  // the user clicked "Retry runner install" on the toast.
  ipcMain.handle('deskagent:update:runner:install', async () => {
    if (!runnerUpdater) return { ok: false, reason: 'no-runner-updater' }
    try {
      return await runnerUpdater.installPending()
    } catch (error) {
      return { ok: false, reason: String(error?.message || error) }
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
