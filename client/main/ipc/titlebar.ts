import type { BrowserWindow, IpcMain, TitleBarOverlayOptions } from 'electron'

export function isHexColor(value: unknown): value is string {
  return typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value)
}

export interface TitlebarIpcDeps {
  getTitleBarOverlayOptions: () => TitleBarOverlayOptions
  getToolWindow: () => BrowserWindow | null | undefined
  ipcMain: IpcMain
  setRendererTitleBarTheme: (theme: { background: string; foreground: string }) => void
}

export function registerTitlebarIpc({
  getTitleBarOverlayOptions,
  getToolWindow,
  ipcMain,
  setRendererTitleBarTheme
}: TitlebarIpcDeps): void {
  ipcMain.on('spiritagent:titlebar-theme', (event, payload) => {
    const tool = getToolWindow()

    // Only the tool window's renderer may restyle its overlay — the sprite
    // window boots the light theme and would paint a light strip on the dark
    // tool UI.
    if (!tool || tool.isDestroyed() || tool.webContents !== event.sender) {
      return
    }

    if (!payload || !isHexColor(payload.background) || !isHexColor(payload.foreground)) {
      return
    }

    setRendererTitleBarTheme({
      background: payload.background,
      foreground: payload.foreground
    })

    tool.setTitleBarOverlay?.(getTitleBarOverlayOptions())
  })
}
