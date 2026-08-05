'use strict'

const fs = require('node:fs')
const path = require('node:path')

// Sprite-window control surface for the renderer: click-through toggling,
// dynamic always-on-top (dropped while the chat dialog is focused so other
// apps can cover it), and rest-position persistence. Every handler no-ops
// when the sprite window is gone (hidden/destroyed) rather than throwing —
// the renderer may race a call against teardown.
const POSITION_FILE = 'companion-position.json'

function readRestPosition(userDataDir) {
  try {
    const raw = fs.readFileSync(path.join(userDataDir, POSITION_FILE), 'utf8')
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed.x === 'number' && typeof parsed.y === 'number') return parsed
  } catch {
    // No saved position yet — caller falls back to the default rest corner.
  }
  return null
}

function registerSpriteIpc({ ipcMain, deps }) {
  const { getSpriteWindow, getUserDataDir } = deps

  const withWindow = fn => {
    const win = getSpriteWindow()
    if (win && !win.isDestroyed()) fn(win)
  }

  // Click-through: the screen-sized transparent window stays
  // setIgnoreMouseEvents(true, {forward:true}) so the desktop shows through,
  // forwarding mousemove so the renderer can detect re-entering an interactive
  // region. Toggling to false over the sprite / dialog / bubble captures input.
  ipcMain.handle('deskagent:sprite:set-ignore-mouse-events', async (_event, payload) => {
    const ignore = Boolean(payload?.ignore)
    withWindow(win => win.setIgnoreMouseEvents(ignore, { forward: ignore && payload?.forward !== false }))
  })

  // Dialog open → drop alwaysOnTop so a maximized app can cover the conversation
  // (the user is focused on it anyway); dialog close → restore ambient top-most.
  ipcMain.handle('deskagent:sprite:set-always-on-top', async (_event, payload) => {
    const on = Boolean(payload?.on)
    withWindow(win => win.setAlwaysOnTop(on, on ? 'floating' : undefined))
  })

  ipcMain.handle('deskagent:sprite:get-position', async () => {
    const dir = getUserDataDir()
    if (!dir) return null
    return readRestPosition(dir)
  })

  ipcMain.handle('deskagent:sprite:set-position', async (_event, payload) => {
    if (!payload || typeof payload.x !== 'number' || typeof payload.y !== 'number') return
    try {
      const dir = getUserDataDir()
      fs.mkdirSync(dir, { recursive: true })
      fs.writeFileSync(path.join(dir, POSITION_FILE), JSON.stringify({ x: payload.x, y: payload.y }, null, 2))
    } catch {
      // Rest-position persistence is best-effort.
    }
  })
}

module.exports = { registerSpriteIpc, readRestPosition }
