import fs from 'node:fs'
import path from 'node:path'

import { IPC, type SpiritAgentSelectPathsOptions } from '@ipc/contracts'
import type { BrowserWindow, Dialog, IpcMain } from 'electron'

import { dataUrlFromBuffer } from '../shared/mime'

export interface FilesIpcDeps {
  electron: {
    dialog: Dialog
    getMainWindow: () => BrowserWindow | null | undefined
  }
  hardening: {
    DATA_URL_READ_MAX_BYTES: number
    resolveReadableFileForIpc: (
      filePath: string,
      options?: { maxBytes?: number; purpose?: string }
    ) => Promise<{ resolvedPath: string; stat: fs.Stats }>
  }
  ipcMain: IpcMain
  mimeTypeForPath: (filePath: string) => string
}

export function registerFilesIpc({ electron, hardening, ipcMain, mimeTypeForPath }: FilesIpcDeps): void {
  const { dialog, getMainWindow } = electron

  ipcMain.handle(IPC.invoke.readFileDataUrl, async (_event, filePath: string) => {
    const { resolvedPath } = await hardening.resolveReadableFileForIpc(filePath, {
      maxBytes: hardening.DATA_URL_READ_MAX_BYTES,
      purpose: 'File preview'
    })

    const data = await fs.promises.readFile(resolvedPath)

    return dataUrlFromBuffer(data, mimeTypeForPath(resolvedPath))
  })

  ipcMain.handle(IPC.invoke.selectPaths, async (_event, options: SpiritAgentSelectPathsOptions = {}) => {
    const properties: Array<'multiSelections' | 'openDirectory' | 'openFile'> = options?.directories
      ? ['openDirectory']
      : ['openFile']

    if (options?.multiple !== false) {
      properties.push('multiSelections')
    }

    let resolvedDefaultPath: string | undefined

    if (options?.defaultPath) {
      try {
        resolvedDefaultPath = path.resolve(String(options.defaultPath))
      } catch {
        resolvedDefaultPath = undefined
      }
    }

    const mainWin = getMainWindow()

    const openOptions = {
      defaultPath: resolvedDefaultPath,
      filters: Array.isArray(options?.filters) ? options.filters : undefined,
      properties,
      title: options?.title || 'Add context'
    }

    const result = mainWin
      ? await dialog.showOpenDialog(mainWin, openOptions)
      : await dialog.showOpenDialog(openOptions)

    if (result.canceled) {
      return []
    }

    return result.filePaths
  })
}
