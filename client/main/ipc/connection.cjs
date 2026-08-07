'use strict'

// Backend connection resolver + REST proxy + boot-progress snapshot.
function registerConnectionIpc({
  ipcMain,
  ensureBackend,
  resetBackendCache,
  getBootProgressState,
  fetchJson,
  resolvePathTimeoutMs,
  resolveTimeoutMs,
  defaultFetchTimeoutMs
}) {
  ipcMain.handle('deskagent:connection', async () => ensureBackend())
  ipcMain.handle('deskagent:gateway:ws-url', async () => {
    const connection = await ensureBackend()
    return connection.wsUrl
  })
  ipcMain.handle('deskagent:boot-progress:get', async () => getBootProgressState())

  ipcMain.handle('deskagent:api', async (_event, request) => {
    const connection = await ensureBackend()
    const fallback = resolvePathTimeoutMs(request?.path, request?.method, defaultFetchTimeoutMs)
    const timeoutMs = resolveTimeoutMs(request?.timeoutMs, fallback)
    const url = `${connection.baseUrl}${request.path}`
    try {
      return await fetchJson(url, connection.token, {
        method: request?.method,
        body: request?.body,
        timeoutMs
      })
    } catch (error) {
      // Auto-expire session on 401 so the renderer can show the login page
      // instead of a cascade of failing requests.
      if (error?.message?.startsWith('401 ') && connection.token) {
        try {
          _event.sender.send('deskagent:auth:session-expired')
        } catch {
          /* window may have been destroyed */
        }
      }
      throw error
    }
  })

  // Expose cache reset so auth IPC can invalidate the cached connection
  // after login/logout when the JWT changes.
  return { resetBackendCache: resetBackendCache || (() => {}) }
}

module.exports = { registerConnectionIpc }
