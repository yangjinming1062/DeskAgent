import { IPC } from '@ipc/contracts'
import type { IpcMain } from 'electron'

import * as store from '../shared/lib/runner-config-store'

interface PrefsIpcDeps {
  ipcMain: IpcMain
}

// 渲染层偏好写穿透终点：把点键合入配置镜像，乘既有管道
// （镜像原子写 + runner 推送 + 云端防抖上云，见 shared/lib/config-sync.ts）。
// 拖拽类高频源由渲染侧防抖（floating-panel 600ms），此处立即合入。
// 只放行偏好节前缀——terminal 等本机节不允许经此通道写入。
const ALLOWED_KEY_PREFIXES = ['companion.', 'shortcuts.', 'ui.'] as const

export function registerPrefsIpc({ ipcMain }: PrefsIpcDeps): void {
  ipcMain.on(IPC.send.prefsSet, (_event, payload: unknown) => {
    if (!payload || typeof payload !== 'object') {
      return
    }

    const { key, value } = payload as { key?: unknown; value?: unknown }

    if (typeof key !== 'string' || !ALLOWED_KEY_PREFIXES.some(prefix => key.startsWith(prefix))) {
      return
    }

    const keyPath = key.split('.')

    if (keyPath.some(part => part.length === 0)) {
      return
    }

    void store.patch(keyPath, { value })
  })
}
