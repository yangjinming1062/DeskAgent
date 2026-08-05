'use strict'

// Thin wrapper around main/entry.cjs::openExternalUrl — caller owns the
// platform-specific dispatch; here we just call it and surface false-return
// as a renderer-visible error.
function registerExternalIpc({ ipcMain, openExternalUrl }) {
  ipcMain.handle('deskagent:openExternal', (_event, url) => {
    if (!openExternalUrl(url)) {
      throw new Error('Invalid external URL')
    }
  })
}

module.exports = { registerExternalIpc }
