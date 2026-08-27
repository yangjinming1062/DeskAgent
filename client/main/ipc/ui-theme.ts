import { IPC, SPIRITAGENT_UI_THEMES, type SpiritAgentUiTheme } from '@ipc/contracts'
import type { BrowserWindow, IpcMain } from 'electron'

import * as store from '../shared/lib/runner-config-store'

interface UiThemeIpcDeps {
  getMainWindow: () => BrowserWindow | null | undefined
  getToolWindow: () => BrowserWindow | null | undefined
  ipcMain: IpcMain
}

// 主题切换入口只存在于工具窗（Hub 设置面板）——sender 守卫只认工具窗。
// 主进程不持有主题状态：校验 id 合法后原样广播给两个窗口；ui.theme 节漏斗进配置镜像，
// 随云端同步管道上云（渲染层 localStorage 仍是各窗口的即时缓存）。
export function registerUiThemeIpc({ getMainWindow, getToolWindow, ipcMain }: UiThemeIpcDeps): void {
  ipcMain.on(IPC.send.uiTheme, (event, payload: SpiritAgentUiTheme) => {
    const tool = getToolWindow()

    if (!tool || tool.isDestroyed() || tool.webContents !== event.sender) {
      return
    }

    if (typeof payload !== 'string' || !SPIRITAGENT_UI_THEMES.includes(payload)) {
      return
    }

    void store.mutate(config => {
      config.ui = { ...(config.ui as Record<string, unknown> | undefined), theme: payload }
    })

    for (const win of [getMainWindow(), getToolWindow()]) {
      if (win && !win.isDestroyed() && !win.webContents.isDestroyed()) {
        win.webContents.send(IPC.event.uiThemeChanged, { theme: payload })
      }
    }
  })
}
