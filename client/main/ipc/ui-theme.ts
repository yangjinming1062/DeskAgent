import { IPC, SPIRITAGENT_UI_THEMES, type SpiritAgentUiTheme } from '@ipc/contracts'
import type { BrowserWindow, IpcMain } from 'electron'

interface UiThemeIpcDeps {
  getMainWindow: () => BrowserWindow | null | undefined
  getToolWindow: () => BrowserWindow | null | undefined
  ipcMain: IpcMain
}

// 主题切换入口只存在于工具窗（Hub 设置面板）——sender 守卫与 titlebar.ts 同理。
// 主进程不持有主题状态：校验 id 合法后原样广播给两个窗口，持久化在渲染层 localStorage。
export function registerUiThemeIpc({ getMainWindow, getToolWindow, ipcMain }: UiThemeIpcDeps): void {
  ipcMain.on(IPC.send.uiTheme, (event, payload: SpiritAgentUiTheme) => {
    const tool = getToolWindow()

    if (!tool || tool.isDestroyed() || tool.webContents !== event.sender) {
      return
    }

    if (typeof payload !== 'string' || !SPIRITAGENT_UI_THEMES.includes(payload)) {
      return
    }

    for (const win of [getMainWindow(), getToolWindow()]) {
      if (win && !win.isDestroyed() && !win.webContents.isDestroyed()) {
        win.webContents.send(IPC.event.uiThemeChanged, { theme: payload })
      }
    }
  })
}
