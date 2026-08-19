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

// Guard for mainWindow.webContents.send(...): skip if destroyed (race during shutdown/reload).
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

// Write-then-rename so a crash mid-write leaves the previous file intact.
// Unlinks the .tmp on failure to avoid accumulating orphans across crashed saves.
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

// Resolves after `ms` milliseconds.
export function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}
