'use strict'

const path = require('node:path')
const fs = require('node:fs')
const { looksBinary } = require('../shared/utils.cjs')

const TEXT_PREVIEW_MAX_BYTES = 512 * 1024

function registerFilesIpc({ ipcMain, electron, hardening, mimeTypeForPath, previewLanguageByExt }) {
  const { dialog, getMainWindow } = electron

  ipcMain.handle('deskagent:readFileDataUrl', async (_event, filePath) => {
    const { resolvedPath } = await hardening.resolveReadableFileForIpc(filePath, {
      maxBytes: hardening.DATA_URL_READ_MAX_BYTES,
      purpose: 'File preview'
    })
    const data = await fs.promises.readFile(resolvedPath)
    return `data:${mimeTypeForPath(resolvedPath)};base64,${data.toString('base64')}`
  })

  ipcMain.handle('deskagent:readFileText', async (_event, filePath) => {
    const { resolvedPath, stat } = await hardening.resolveReadableFileForIpc(filePath, {
      maxBytes: hardening.TEXT_PREVIEW_SOURCE_MAX_BYTES,
      purpose: 'Text preview'
    })
    const ext = path.extname(resolvedPath).toLowerCase()
    const handle = await fs.promises.open(resolvedPath, 'r')
    const bytesToRead = Math.min(stat.size, TEXT_PREVIEW_MAX_BYTES)

    try {
      const buffer = Buffer.alloc(bytesToRead)
      const { bytesRead } = await handle.read(buffer, 0, bytesToRead, 0)

      return {
        binary: looksBinary(buffer.subarray(0, Math.min(bytesRead, 4096))),
        byteSize: stat.size,
        language: previewLanguageByExt[ext] || 'text',
        mimeType: mimeTypeForPath(resolvedPath),
        path: resolvedPath,
        text: buffer.subarray(0, bytesRead).toString('utf8'),
        truncated: stat.size > TEXT_PREVIEW_MAX_BYTES
      }
    } finally {
      await handle.close()
    }
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
