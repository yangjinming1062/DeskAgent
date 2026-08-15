import fs from 'node:fs'
import path from 'node:path'

import type { BrowserWindow, Dialog, IpcMain } from 'electron'

import type { DeskAgentSelectPathsOptions } from '../../renderer/shared/types/global'
import { dataUrlFromBuffer } from '../shared/mime'

export interface FilesIpcDeps {
  electron: {
    dialog: Dialog
    getMainWindow: () => BrowserWindow | null | undefined
  }
  hardening: {
    DATA_URL_READ_MAX_BYTES: number
    resolveReadableFileForIpc: (filePath: string, options: any) => Promise<{ resolvedPath: string; stat: any }>
  }
  ipcMain: IpcMain
  mimeTypeForPath: (filePath: string) => string
}

export function registerFilesIpc({ electron, hardening, ipcMain, mimeTypeForPath }: FilesIpcDeps): void {
  const { dialog, getMainWindow } = electron

  ipcMain.handle('deskagent:readFileDataUrl', async (_event, filePath) => {
    const { resolvedPath } = await hardening.resolveReadableFileForIpc(filePath, {
      maxBytes: hardening.DATA_URL_READ_MAX_BYTES,
      purpose: 'File preview'
    })

    const data = await fs.promises.readFile(resolvedPath)

    return dataUrlFromBuffer(data, mimeTypeForPath(resolvedPath))
  })

  ipcMain.handle('deskagent:selectPaths', async (_event, options: DeskAgentSelectPathsOptions = {}) => {
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

    const result = await dialog.showOpenDialog((mainWin as any) ?? null, {
      defaultPath: resolvedDefaultPath,
      filters: Array.isArray(options?.filters) ? options.filters : undefined,
      properties,
      title: options?.title || 'Add context'
    })

    if (result.canceled) {
      return []
    }

    return result.filePaths
  })
}
