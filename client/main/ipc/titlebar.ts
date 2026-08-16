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
  ipcMain.on('spiritagent:titlebar-theme', (_event, payload) => {
    if (!payload || !isHexColor(payload.background) || !isHexColor(payload.foreground)) {
      return
    }

    setRendererTitleBarTheme({
      background: payload.background,
      foreground: payload.foreground
    })

    getToolWindow()?.setTitleBarOverlay?.(getTitleBarOverlayOptions())
  })
}
