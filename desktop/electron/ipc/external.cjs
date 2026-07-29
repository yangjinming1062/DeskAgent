'use strict'

// Thin wrapper around main.cjs's openExternalUrl — it owns all the
// file:// / http(s) / mailto / WSL→Windows dispatch logic. Here we just call it
// and convert a false return into a renderer-visible error.
function registerExternalIpc({ ipcMain, openExternalUrl }) {
  ipcMain.handle('deskagent:openExternal', (_event, url) => {
    if (!openExternalUrl(url)) {
      throw new Error('Invalid external URL')
    }
  })
}

module.exports = { registerExternalIpc }
