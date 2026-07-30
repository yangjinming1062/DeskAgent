'use strict'

// Composer image persistence: write a remote image to disk via Save dialog,
// write a raw image buffer straight to userData/composer-images, and the
// dedicated clipboard-image variant (clipboard handling lives in
// ipc/clipboard.cjs because the read-clipboard step is its own surface).
function registerImagesIpc({ ipcMain, saveImageFromUrl, writeComposerImage }) {
  ipcMain.handle('deskagent:saveImageFromUrl', (_event, url) => saveImageFromUrl(String(url || '')))

  ipcMain.handle('deskagent:saveImageBuffer', async (_event, payload) => {
    const data = payload?.data
    if (!data) throw new Error('saveImageBuffer: missing data')

    const buffer = Buffer.isBuffer(data) ? data : Buffer.from(data)
    return writeComposerImage(buffer, payload?.ext || '.png')
  })
}

module.exports = { registerImagesIpc }
