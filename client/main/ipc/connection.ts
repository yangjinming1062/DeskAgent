import fsp from 'node:fs/promises'

import { type DesktopBootProgress, IPC, type SpiritAgentApiRequest, type SpiritAgentConnection } from '@ipc/contracts'
import type { IpcMain } from 'electron'

import { dataUrlFromBuffer } from '../shared/mime'

import type { ModelDiskCache } from './model-disk-cache'

export interface ConnectionIpcDeps {
  defaultFetchTimeoutMs?: number
  ensureBackend: () => Promise<SpiritAgentConnection>
  fetchImpl?: typeof globalThis.fetch
  fetchJson: (
    url: string,
    token?: string,
    options?: { body?: unknown; method?: string; timeoutMs?: number }
  ) => Promise<unknown>
  getBootProgressState: () => DesktopBootProgress
  ipcMain: IpcMain
  mintWsTicket?: (baseUrl: string, token: string | null) => Promise<string | null>
  modelDiskCache?: null | ModelDiskCache
  resolvePathTimeoutMs: (path?: string, method?: string, fallbackMs?: number) => number
  resolveTimeoutMs: (timeoutMs?: number | string | null, fallbackMs?: number) => number
  setCachedWsUrl?: (wsUrl: string) => void
}

export function registerConnectionIpc({
  defaultFetchTimeoutMs = 15_000,
  ensureBackend,
  fetchImpl,
  fetchJson,
  getBootProgressState,
  ipcMain,
  mintWsTicket,
  modelDiskCache,
  resolvePathTimeoutMs,
  resolveTimeoutMs,
  setCachedWsUrl
}: ConnectionIpcDeps): void {
  ipcMain.handle(IPC.invoke.connection, async () => ensureBackend())
  ipcMain.handle(IPC.invoke.gatewayWsUrl, async () => {
    const connection = await ensureBackend()

    if (mintWsTicket && connection.token) {
      const fresh = await mintWsTicket(connection.baseUrl, connection.token).catch(() => null)

      if (fresh) {
        const wsBase = connection.baseUrl.replace(/^http/, 'ws')
        const freshWsUrl = `${wsBase}/api/chat/ws?ticket=${fresh}`
        setCachedWsUrl?.(freshWsUrl)

        return freshWsUrl
      }
    }

    return connection.wsUrl
  })
  ipcMain.handle(IPC.invoke.bootProgressGet, async () => getBootProgressState())

  ipcMain.handle(IPC.invoke.api, async (_event, request: SpiritAgentApiRequest) => {
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
          _event.sender.send(IPC.event.authSessionExpired)
        } catch {
          /* window may have been destroyed */
        }
      }

      throw error
    }
  })

  ipcMain.handle(IPC.invoke.apiAsset, async (_event, request?: { url?: string }) => {
    const connection = await ensureBackend()
    const raw = String(request?.url || '')

    if (!raw) {
      throw new Error('asset url is required')
    }

    const { pathname, search } = new URL(raw, connection.baseUrl)
    const pathAndQuery = `${pathname}${search}`

    const timeoutMs = defaultFetchTimeoutMs
    const caller = fetchImpl || globalThis.fetch

    const res = await caller(`${connection.baseUrl}${pathAndQuery}`, {
      headers: { ...(connection.token ? { Authorization: `Bearer ${connection.token}` } : {}) },
      signal: AbortSignal.timeout(timeoutMs)
    })

    if (!res.ok) {
      if (res.status === 401 && connection.token) {
        try {
          _event.sender.send(IPC.event.authSessionExpired)
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
    IPC.invoke.apiAssetModelUrl,
    async (_event, request: { contentHash?: string; url: string }): Promise<string> => {
      const connection = await ensureBackend()
      const raw = String(request.url || '')

      if (!raw) {
        throw new Error('asset url is required')
      }

      if (!modelDiskCache) {
        throw new Error('apiAssetModelUrl requires the model disk cache; ensure spiritagentHome is configured')
      }

      try {
        const cached = await modelDiskCache.ensureCached({
          baseUrl: connection.baseUrl,
          contentHash: request.contentHash,
          fetchFn: fetchImpl,
          token: connection.token || undefined,
          url: raw
        })

        const normalizedPath = cached.filePath.replace(/\\/g, '/')

        return `spiritagent-media:///${normalizedPath}`
      } catch (error: unknown) {
        // 与兄弟 handler `apiAsset` / `apiAssetBuffer` 对齐:401 触发广播,
        // 渲染层 `onSessionExpired` 监听器可触发重新登录。
        const message = error instanceof Error ? error.message : String(error)

        if (message.startsWith('401 ') && connection.token) {
          try {
            _event.sender.send(IPC.event.authSessionExpired)
          } catch {
            /* window may have been destroyed */
          }
        }

        throw error
      }
    }
  )

  ipcMain.handle(IPC.invoke.apiAssetBuffer, async (_event, request?: { contentHash?: string; url?: string }) => {
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
        fetchFn: fetchImpl,
        token: connection.token || undefined,
        url: raw
      })

      return await fsp.readFile(cached.filePath)
    }

    const pathAndQuery = `${pathname}${search}`
    const timeoutMs = defaultFetchTimeoutMs
    const caller = fetchImpl || globalThis.fetch

    const res = await caller(`${connection.baseUrl}${pathAndQuery}`, {
      headers: { ...(connection.token ? { Authorization: `Bearer ${connection.token}` } : {}) },
      signal: AbortSignal.timeout(timeoutMs)
    })

    if (!res.ok) {
      if (res.status === 401 && connection.token) {
        try {
          _event.sender.send(IPC.event.authSessionExpired)
        } catch {
          /* window may have been destroyed */
        }
      }

      const text = await res.text().catch(() => '')
      throw new Error(`${res.status} ${pathname}: ${text || res.statusText}`)
    }

    return Buffer.from(await res.arrayBuffer())
  })
}
