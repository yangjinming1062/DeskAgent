'use strict'

const fsp = require('node:fs').promises
const { dataUrlFromBuffer } = require('../shared/mime.cjs')

// Backend connection resolver + REST proxy + model disk cache.
function registerConnectionIpc({
  ipcMain,
  ensureBackend,
  resetBackendCache,
  getBootProgressState,
  fetchJson,
  resolvePathTimeoutMs,
  resolveTimeoutMs,
  defaultFetchTimeoutMs,
  modelDiskCache
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

    const timeoutMs = defaultFetchTimeoutMs
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

  // Cache-aware asset downloader: returns the local disk cache path for large assets.
  ipcMain.handle('deskagent:api:asset-cached-path', async (_event, request) => {
    const connection = await ensureBackend()
    const raw = String(request?.url || '')
    if (!raw) throw new Error('asset url is required')
    if (!modelDiskCache) throw new Error('model disk cache is unavailable')

    return await modelDiskCache.ensureCached({
      url: raw,
      contentHash: request?.contentHash,
      token: connection.token,
      baseUrl: connection.baseUrl
    })
  })

  // Like ``api:asset`` but returns raw bytes via structured clone.
  // For 3D models and large assets, transparently leverages modelDiskCache
  // to support HTTP Range resumption and avoid repeated downloads.
  ipcMain.handle('deskagent:api:asset-buffer', async (_event, request) => {
    const connection = await ensureBackend()
    const raw = String(request?.url || '')
    if (!raw) throw new Error('asset url is required')

    const { pathname, search } = new URL(raw, connection.baseUrl)
    const isModel =
      pathname.includes('/model/file/') || pathname.includes('/companion-models/') || Boolean(request?.contentHash)

    if (modelDiskCache && isModel) {
      const cached = await modelDiskCache.ensureCached({
        url: raw,
        contentHash: request?.contentHash,
        token: connection.token,
        baseUrl: connection.baseUrl
      })
      return await fsp.readFile(cached.filePath)
    }

    const pathAndQuery = `${pathname}${search}`
    const timeoutMs = defaultFetchTimeoutMs
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
