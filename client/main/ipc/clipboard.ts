import type { Clipboard, IpcMain } from 'electron'

export interface ClipboardIpcDeps {
  electron: { clipboard: Clipboard }
  ipcMain: IpcMain
  writeComposerImage: (buffer: Buffer, ext: string) => Promise<string>
}

export function registerClipboardIpc({ electron, ipcMain, writeComposerImage }: ClipboardIpcDeps): void {
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
