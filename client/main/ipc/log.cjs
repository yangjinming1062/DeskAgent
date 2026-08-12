'use strict'

// Renderer → main structured logging. The renderer's `log.warn/error/info`
// (renderer/shared/lib/log.ts) forwards `{ level, scope, args }` here; we
// format one line and hand it to the main process's `rememberLog` so renderer
// diagnostics land in the desktop log file alongside main-process entries.
function formatRendererLog(payload) {
  const { scope, args } = payload ?? {}
  const parts = (Array.isArray(args) ? args : [args]).map(a => {
    if (a == null) return String(a)
    if (typeof a === 'object' && a.message) return a.message
    if (typeof a === 'object') {
      try {
        return JSON.stringify(a)
      } catch {
        return String(a)
      }
    }
    return String(a)
  })
  return `[renderer:${scope}] ${parts.join(' ')}`
}

function registerLogIpc({ ipcMain, log }) {
  ipcMain.handle('deskagent:log:emit', (_event, payload) => {
    log(formatRendererLog(payload))
  })
}

module.exports = { registerLogIpc }
