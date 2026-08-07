'use strict'

function isHexColor(value) {
  return typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value)
}

// Renderer pushes its desired titlebar colors (rendered via custom titlebar
// overlay); we validate the hex strings before applying them to the
// BrowserWindow's native overlay. Validated state lives in main.cjs.
//
// Live setTitleBarOverlay targets the framed tool window ONLY: the sprite
// window (mainWindow) is frameless + transparent with no titleBarOverlay, so
// calling setTitleBarOverlay on it throws "Titlebar overlay is not enabled" —
// and as an ipcMain handler that surfaces as a main-process uncaught-exception
// dialog. Both windows still push their theme here so the cached colors feed
// getTitleBarOverlayOptions() for the next tool-window creation.
function registerTitlebarIpc({ ipcMain, getToolWindow, getTitleBarOverlayOptions, setRendererTitleBarTheme }) {
  ipcMain.on('deskagent:titlebar-theme', (_event, payload) => {
    if (!payload || !isHexColor(payload.background) || !isHexColor(payload.foreground)) {
      return
    }

    setRendererTitleBarTheme({
      background: payload.background,
      foreground: payload.foreground
    })

    getToolWindow()?.setTitleBarOverlay?.(getTitleBarOverlayOptions())
  })
}

module.exports = { registerTitlebarIpc }
