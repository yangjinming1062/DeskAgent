'use strict'

const path = require('node:path')
const fs = require('node:fs')

const { dataUrlFromBuffer } = require('../shared/mime.cjs')

function registerFilesIpc({ ipcMain, electron, hardening, mimeTypeForPath }) {
  const { dialog, getMainWindow } = electron

  ipcMain.handle('deskagent:readFileDataUrl', async (_event, filePath) => {
    const { resolvedPath } = await hardening.resolveReadableFileForIpc(filePath, {
      maxBytes: hardening.DATA_URL_READ_MAX_BYTES,
      purpose: 'File preview'
    })
    const data = await fs.promises.readFile(resolvedPath)
    return dataUrlFromBuffer(data, mimeTypeForPath(resolvedPath))
  })

  ipcMain.handle('deskagent:selectPaths', async (_event, options = {}) => {
    const properties = options?.directories ? ['openDirectory'] : ['openFile']
    if (options?.multiple !== false) properties.push('multiSelections')

    let resolvedDefaultPath
    if (options?.defaultPath) {
      try {
        resolvedDefaultPath = path.resolve(String(options.defaultPath))
      } catch {
        resolvedDefaultPath = undefined
      }
    }

    const result = await dialog.showOpenDialog(getMainWindow() ?? null, {
      title: options?.title || 'Add context',
      defaultPath: resolvedDefaultPath,
      properties,
      filters: Array.isArray(options?.filters) ? options.filters : undefined
    })

    if (result.canceled) return []
    return result.filePaths
  })
}

module.exports = { registerFilesIpc }
