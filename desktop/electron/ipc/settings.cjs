'use strict'

const path = require('node:path')
const fs = require('node:fs')

// User-configurable default project directory. The renderer reads it on
// settings mount and seeds the value into the picker; writing back persists
// it via writeDefaultProjectDir so resolveDeskAgentCwd picks it up on the next
// session spawn (no app restart needed).
function registerSettingsIpc({ ipcMain, electron, readDefaultProjectDir, writeDefaultProjectDir }) {
  const { app, dialog } = electron

  ipcMain.handle('deskagent:setting:defaultProjectDir:get', async () => ({
    dir: readDefaultProjectDir(),
    defaultLabel: path.join(app.getPath('home'), 'deskagent-projects')
  }))

  ipcMain.handle('deskagent:setting:defaultProjectDir:set', async (_event, dir) => {
    const next = typeof dir === 'string' && dir.trim() ? dir.trim() : null

    if (next) {
      try {
        fs.mkdirSync(next, { recursive: true })
      } catch (error) {
        throw new Error(`Could not create directory: ${error.message}`)
      }
    }

    writeDefaultProjectDir(next)

    return { dir: next }
  })

  ipcMain.handle('deskagent:setting:defaultProjectDir:pick', async () => {
    const result = await dialog.showOpenDialog({
      title: 'Choose default project directory',
      properties: ['openDirectory', 'createDirectory'],
      defaultPath: readDefaultProjectDir() || app.getPath('home')
    })

    if (result.canceled || result.filePaths.length === 0) {
      return { canceled: true, dir: null }
    }

    return { canceled: false, dir: result.filePaths[0] }
  })
}

module.exports = { registerSettingsIpc }
