'use strict'

// Two channels: write text to system clipboard (used by the composer + context
// menu), and save the current clipboard image as a composer image. Image save
// delegates to writeComposerImage in main.cjs — it's the only place that knows
// the userData/composer-images path.
function registerClipboardIpc({ ipcMain, electron, writeComposerImage }) {
  const { clipboard } = electron

  ipcMain.handle('deskagent:writeClipboard', (_event, text) => {
    clipboard.writeText(String(text || ''))
    return true
  })

  ipcMain.handle('deskagent:saveClipboardImage', async () => {
    const image = clipboard.readImage()
    if (!image || image.isEmpty()) {
      return ''
    }

    return writeComposerImage(image.toPNG(), '.png')
  })
}

module.exports = { registerClipboardIpc }
