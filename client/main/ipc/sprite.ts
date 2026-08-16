import fs from 'node:fs'
import path from 'node:path'

import type { BrowserWindow, IpcMain } from 'electron'

export const POSITION_FILE = 'companion-position.json'

export function readRestPosition(userDataDir?: string): null | { x: number; y: number } {
  if (!userDataDir) {
    return null
  }

  try {
    const raw = fs.readFileSync(path.join(userDataDir, POSITION_FILE), 'utf8')
    const parsed = JSON.parse(raw) as { x?: unknown; y?: unknown }

    if (parsed && typeof parsed.x === 'number' && typeof parsed.y === 'number') {
      return { x: parsed.x, y: parsed.y }
    }
  } catch {
    // No saved position yet
  }

  return null
}

export interface SpriteIpcDeps {
  getSpriteWindow: () => BrowserWindow | null | undefined
  getUserDataDir: () => string
}

export function registerSpriteIpc({ deps, ipcMain }: { deps: SpriteIpcDeps; ipcMain: IpcMain }): void {
  const { getSpriteWindow, getUserDataDir } = deps

  const withWindow = (fn: (win: BrowserWindow) => void) => {
    const win = getSpriteWindow()

    if (win && !win.isDestroyed()) {
      fn(win)
    }
  }

  ipcMain.handle(
    'spiritagent:sprite:set-ignore-mouse-events',
    async (_event, payload?: { forward?: boolean; ignore: boolean }) => {
      const ignore = Boolean(payload?.ignore)
      withWindow(win => win.setIgnoreMouseEvents(ignore, { forward: ignore && payload?.forward !== false }))
    }
  )

  ipcMain.handle('spiritagent:sprite:set-always-on-top', async (_event, payload?: { on: boolean }) => {
    const on = Boolean(payload?.on)
    withWindow(win => win.setAlwaysOnTop(on, on ? 'floating' : undefined))
  })

  ipcMain.handle('spiritagent:sprite:get-position', async () => {
    const dir = getUserDataDir()

    if (!dir) {
      return null
    }

    return readRestPosition(dir)
  })

  ipcMain.handle('spiritagent:sprite:set-position', async (_event, payload?: { x: number; y: number }) => {
    if (!payload || typeof payload.x !== 'number' || typeof payload.y !== 'number') {
      return
    }

    try {
      const dir = getUserDataDir()
      fs.mkdirSync(dir, { recursive: true })
      fs.writeFileSync(path.join(dir, POSITION_FILE), JSON.stringify({ x: payload.x, y: payload.y }, null, 2))
    } catch {
      // Best effort
    }
  })
}
