import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'

import type { IpcEventChannel, IpcEventContract } from '@ipc/contracts'
import type { BrowserWindow } from 'electron'

export function fileExists(filePath: string): boolean {
  try {
    return fs.statSync(filePath).isFile()
  } catch {
    return false
  }
}

export function directoryExists(filePath: string): boolean {
  try {
    return fs.statSync(filePath).isDirectory()
  } catch {
    return false
  }
}

// 守护 webContents.send：关闭/重载期间主窗口可能已销毁。
// channel 收紧为 `IpcEventChannel`(`webContents.send` 是主→渲单向事件),
// 不联合 `IpcSendChannel`(那是渲染→主的 `ipcRenderer.send` 方向)。
export function sendToMain<C extends IpcEventChannel>(
  mainWindow: BrowserWindow | null | undefined,
  channel: C,
  ...payload: IpcEventContract[C]
): void {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return
  }

  const { webContents } = mainWindow

  if (!webContents || webContents.isDestroyed()) {
    return
  }

  webContents.send(channel, ...payload)
}

// 先写 .tmp 再重命名，崩溃时旧文件保持完整；失败时清理残留 tmp。
export async function atomicWriteFile(targetPath: string, content: Buffer | string | Uint8Array): Promise<void> {
  await fs.promises.mkdir(path.dirname(targetPath), { recursive: true })
  const tmpPath = `${targetPath}.${process.pid}.${crypto.randomUUID()}.tmp`

  try {
    await fs.promises.writeFile(tmpPath, content)
    await fs.promises.rename(tmpPath, targetPath)
  } catch (error) {
    await fs.promises.unlink(tmpPath).catch(() => {})
    throw error
  }
}

export function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}
