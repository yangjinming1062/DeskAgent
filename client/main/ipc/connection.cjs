'use strict'

const { dataUrlFromBuffer } = require('../shared/mime.cjs')
const { MODEL_FILE_PATH_PATTERN, MODEL_FILE_TIMEOUT_MS } = require('../security/hardening.cjs')

// Match the signed-URL host strip below so a large GLB gets a 60s budget.
function isModelFilePath(pathname) {
  return typeof pathname === 'string' && MODEL_FILE_PATH_PATTERN.test(pathname)
}

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

  // Signed asset URLs carry a host the renderer may not reach; re-base onto the
  // connection main already resolved and return bytes. See README 通信模型.
  ipcMain.handle('deskagent:api:asset', async (_event, request) => {
    const connection = await ensureBackend()
    const raw = String(request?.url || '')
    if (!raw) throw new Error('asset url is required')

    // Keep only path + query so a stale/unreachable host never leaks through.
    const { pathname, search } = new URL(raw, connection.baseUrl)
    const pathAndQuery = `${pathname}${search}`

    const timeoutMs = isModelFilePath(pathname) ? MODEL_FILE_TIMEOUT_MS : defaultFetchTimeoutMs
    const res = await fetch(`${connection.baseUrl}${pathAndQuery}`, {
      headers: { ...(connection.token ? { Authorization: `Bearer ${connection.token}` } : {}) },
      signal: AbortSignal.timeout(timeoutMs)
    })
    if (!res.ok) {
      // Same signal as the sibling REST proxy so the renderer can show login.
      if (res.status === 401 && connection.token) {
        try {
          _event.sender.send('deskagent:auth:session-expired')
        } catch {
          /* window may have been destroyed */
        }
      }
      const text = await res.text().catch(() => '')
      throw new Error(`${res.status} ${pathname}: ${text || res.statusText}`)
    }
    const mime = res.headers.get('content-type') || 'application/octet-stream'
    return dataUrlFromBuffer(Buffer.from(await res.arrayBuffer()), mime)
  })

  // Like ``api:asset`` but returns the raw bytes via Electron IPC structured
  // clone (no base64). For large binary payloads (GLB, wardrobe PBR textures)
  // where base64 inflation in ``api:asset`` would round-trip a 30 MB GLB into
  // a 40 MB string. Same host-strip + rebase behaviour.
  ipcMain.handle('deskagent:api:asset-buffer', async (_event, request) => {
    const connection = await ensureBackend()
    const raw = String(request?.url || '')
    if (!raw) throw new Error('asset url is required')

    const { pathname, search } = new URL(raw, connection.baseUrl)
    const pathAndQuery = `${pathname}${search}`

    const timeoutMs = isModelFilePath(pathname) ? MODEL_FILE_TIMEOUT_MS : defaultFetchTimeoutMs
    const res = await fetch(`${connection.baseUrl}${pathAndQuery}`, {
      headers: { ...(connection.token ? { Authorization: `Bearer ${connection.token}` } : {}) },
      signal: AbortSignal.timeout(timeoutMs)
    })
    if (!res.ok) {
      if (res.status === 401 && connection.token) {
        try {
          _event.sender.send('deskagent:auth:session-expired')
        } catch {
          /* window may have been destroyed */
        }
      }
      const text = await res.text().catch(() => '')
      throw new Error(`${res.status} ${pathname}: ${text || res.statusText}`)
    }
    return Buffer.from(await res.arrayBuffer())
  })

  // Expose cache reset so auth IPC can invalidate the cached connection
  // after login/logout when the JWT changes.
  return { resetBackendCache: resetBackendCache || (() => {}) }
}

module.exports = { registerConnectionIpc }
