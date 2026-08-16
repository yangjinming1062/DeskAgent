import fsp from 'node:fs/promises'

import type { IpcMain } from 'electron'

import type { DesktopBootProgress, SpiritAgentApiRequest, SpiritAgentConnection } from '../shared/ipc-contracts'
import { dataUrlFromBuffer } from '../shared/mime'

import type { ModelDiskCache } from './model-disk-cache'

export interface ConnectionIpcDeps {
  defaultFetchTimeoutMs?: number
  ensureBackend: () => Promise<SpiritAgentConnection>
  fetchJson: (
    url: string,
    token?: string,
    options?: { body?: unknown; method?: string; timeoutMs?: number }
  ) => Promise<unknown>
  getBootProgressState: () => DesktopBootProgress
  ipcMain: IpcMain
  modelDiskCache?: null | ModelDiskCache
  resetBackendCache?: () => void
  resolvePathTimeoutMs: (path?: string, method?: string, fallbackMs?: number) => number
  resolveTimeoutMs: (timeoutMs?: number | string | null, fallbackMs?: number) => number
}

export function registerConnectionIpc({
  defaultFetchTimeoutMs = 15_000,
  ensureBackend,
  fetchJson,
  getBootProgressState,
  ipcMain,
  modelDiskCache,
  resetBackendCache,
  resolvePathTimeoutMs,
  resolveTimeoutMs
}: ConnectionIpcDeps): { resetBackendCache: () => void } {
  ipcMain.handle('spiritagent:connection', async () => ensureBackend())
  ipcMain.handle('spiritagent:gateway:ws-url', async () => {
    const connection = await ensureBackend()

    return connection.wsUrl
  })
  ipcMain.handle('spiritagent:boot-progress:get', async () => getBootProgressState())

  ipcMain.handle('spiritagent:api', async (_event, request: SpiritAgentApiRequest) => {
    const connection = await ensureBackend()
    const fallback = resolvePathTimeoutMs(request?.path, request?.method, defaultFetchTimeoutMs)
    const timeoutMs = resolveTimeoutMs(request?.timeoutMs, fallback)
    const url = `${connection.baseUrl}${request.path}`

    try {
      return await fetchJson(url, connection.token || undefined, {
        body: request?.body,
        method: request?.method,
        timeoutMs
      })
    } catch (error: unknown) {
      const err = error as { message?: string }

      if (err?.message?.startsWith('401 ') && connection.token) {
        try {
          _event.sender.send('spiritagent:auth:session-expired')
        } catch {
          /* window may have been destroyed */
        }
      }

      throw error
    }
  })

  ipcMain.handle('spiritagent:api:asset', async (_event, request?: { url?: string }) => {
    const connection = await ensureBackend()
    const raw = String(request?.url || '')

    if (!raw) {
      throw new Error('asset url is required')
    }

    const { pathname, search } = new URL(raw, connection.baseUrl)
    const pathAndQuery = `${pathname}${search}`

    const timeoutMs = defaultFetchTimeoutMs

    const res = await fetch(`${connection.baseUrl}${pathAndQuery}`, {
      headers: { ...(connection.token ? { Authorization: `Bearer ${connection.token}` } : {}) },
      signal: AbortSignal.timeout(timeoutMs)
    })

    if (!res.ok) {
      if (res.status === 401 && connection.token) {
        try {
          _event.sender.send('spiritagent:auth:session-expired')
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

  ipcMain.handle(
    'spiritagent:api:asset-cached-path',
    async (_event, request?: { contentHash?: string; url?: string }) => {
      const connection = await ensureBackend()
      const raw = String(request?.url || '')

      if (!raw) {
        throw new Error('asset url is required')
      }

      if (!modelDiskCache) {
        throw new Error('model disk cache is unavailable')
      }

      return await modelDiskCache.ensureCached({
        baseUrl: connection.baseUrl,
        contentHash: request?.contentHash,
        token: connection.token || undefined,
        url: raw
      })
    }
  )

  ipcMain.handle('spiritagent:api:asset-buffer', async (_event, request?: { contentHash?: string; url?: string }) => {
    const connection = await ensureBackend()
    const raw = String(request?.url || '')

    if (!raw) {
      throw new Error('asset url is required')
    }

    const { pathname, search } = new URL(raw, connection.baseUrl)

    const isModel =
      pathname.includes('/model/file/') || pathname.includes('/companion-models/') || Boolean(request?.contentHash)

    if (modelDiskCache && isModel) {
      const cached = await modelDiskCache.ensureCached({
        baseUrl: connection.baseUrl,
        contentHash: request?.contentHash,
        token: connection.token || undefined,
        url: raw
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
          _event.sender.send('spiritagent:auth:session-expired')
        } catch {
          /* window may have been destroyed */
        }
      }

      const text = await res.text().catch(() => '')
      throw new Error(`${res.status} ${pathname}: ${text || res.statusText}`)
    }

    return Buffer.from(await res.arrayBuffer())
  })

  return { resetBackendCache: resetBackendCache || (() => {}) }
}
