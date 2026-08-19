import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'

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

// 守护 mainWindow.webContents.send(...)：若已销毁则跳过（关闭/重载期间的竞态）。
export function sendToMain(mainWindow: BrowserWindow | null | undefined, channel: string, payload?: unknown): void {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return
  }

  const { webContents } = mainWindow

  if (!webContents || webContents.isDestroyed()) {
    return
  }

  webContents.send(channel, payload)
}

// 先写再重命名，确保写入中途崩溃时旧文件保持完整。
// 失败时删除 .tmp，避免因崩溃写入而堆积残留文件。
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

// 在 `ms` 毫秒后 resolve。
export function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}
