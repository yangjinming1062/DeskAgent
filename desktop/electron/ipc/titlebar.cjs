'use strict'

function isHexColor(value) {
  return typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value)
}

// Renderer pushes its desired titlebar colors (rendered via custom titlebar
// overlay); we validate the hex strings before applying them to the
// BrowserWindow's native overlay. Validated state lives in main.cjs.
function registerTitlebarIpc({ ipcMain, getMainWindow, getTitleBarOverlayOptions, setRendererTitleBarTheme }) {
  ipcMain.on('deskagent:titlebar-theme', (_event, payload) => {
    if (!payload || !isHexColor(payload.background) || !isHexColor(payload.foreground)) {
      return
    }

    setRendererTitleBarTheme({
      background: payload.background,
      foreground: payload.foreground
    })

    const window = getMainWindow()
    window?.setTitleBarOverlay?.(getTitleBarOverlayOptions())
  })
}

module.exports = { registerTitlebarIpc }
