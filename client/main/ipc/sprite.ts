import fs from 'node:fs'
import path from 'node:path'

import type { BrowserWindow, IpcMain, Screen } from 'electron'

export const POSITION_FILE = 'companion-position.json'

export interface RestPosition {
  x: number
  y: number
  // Window origin at save time — lets the next launch reopen the sprite window
  // on the monitor where the sprite was left, not just the primary display.
  origin?: { x: number; y: number }
}

export function readRestPosition(userDataDir?: string): null | RestPosition {
  if (!userDataDir) {
    return null
  }

  try {
    const raw = fs.readFileSync(path.join(userDataDir, POSITION_FILE), 'utf8')
    const parsed = JSON.parse(raw) as { origin?: unknown; x?: unknown; y?: unknown }

    if (parsed && typeof parsed.x === 'number' && typeof parsed.y === 'number') {
      const next: RestPosition = { x: parsed.x, y: parsed.y }
      const o = parsed.origin as { x?: unknown; y?: unknown } | null

      if (o && typeof o.x === 'number' && typeof o.y === 'number') {
        next.origin = { x: o.x, y: o.y }
      }

      return next
    }
  } catch {
    // No saved position yet
  }

  return null
}

export interface SpriteIpcDeps {
  getSpriteWindow: () => BrowserWindow | null | undefined
  getUserDataDir: () => string
  screen: Screen
}

export function registerSpriteIpc({ deps, ipcMain }: { deps: SpriteIpcDeps; ipcMain: IpcMain }): void {
  const { getSpriteWindow, getUserDataDir, screen } = deps

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

    const win = getSpriteWindow()
    const b = win && !win.isDestroyed() ? win.getContentBounds() : null
    const origin = b ? { x: b.x, y: b.y } : undefined

    try {
      const dir = getUserDataDir()
      fs.mkdirSync(dir, { recursive: true })
      fs.writeFileSync(path.join(dir, POSITION_FILE), JSON.stringify({ x: payload.x, y: payload.y, origin }))
    } catch {
      // Best effort
    }
  })

  // During a drag the renderer reports out-of-viewport pointer coords once the cursor
  // crosses onto another display — snap the window onto the cursor's display and return
  // both origins so the renderer can remap its drag frame without the sprite jumping.
  ipcMain.handle('spiritagent:sprite:move-to-cursor-display', async () => {
    const win = getSpriteWindow()

    if (!win || win.isDestroyed()) {
      return null
    }

    const target = screen.getDisplayNearestPoint(screen.getCursorScreenPoint())

    if (target.id === screen.getDisplayMatching(win.getBounds()).id) {
      return null
    }

    const from = win.getContentBounds()
    win.setBounds(target.workArea)

    return { from: { x: from.x, y: from.y }, to: { x: target.workArea.x, y: target.workArea.y } }
  })
}
