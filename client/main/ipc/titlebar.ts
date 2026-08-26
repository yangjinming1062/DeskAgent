import { IPC } from '@ipc/contracts'
import type { BrowserWindow, IpcMain, TitleBarOverlayOptions } from 'electron'

function isHexColor(value: unknown): value is string {
  return typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value)
}

interface TitlebarIpcDeps {
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
  ipcMain.on(IPC.send.titleBarTheme, (event, payload) => {
    const tool = getToolWindow()

    // 只有工具窗口的渲染层可以重设覆盖层样式——精灵窗口启动的是浅色主题，
    // 套到深色工具 UI 上会画出一道浅色条。
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
