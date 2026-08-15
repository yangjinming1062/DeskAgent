import type { IpcMain } from 'electron'

import type { FetchFunction } from '../backend/client'
import type { BackendSession, BackendSessionOptions, SessionSnapshot } from '../backend/session'
import type { SafeStorageApi } from '../security/hardening'
import { resolveBackendUrl, writeStoredBackendUrl } from '../shared/config'
import type { DesktopActivatePayload } from '../shared/ipc-contracts'

export interface AuthIpcDeps {
  app: { getPath: (name: string) => string }
  backendSession?: null | BackendSession
  broadcastAuthChanged?: (session: null | SessionSnapshot) => void
  buildClientContext?: () => { client_context?: unknown }
  createBackendSession: (options: BackendSessionOptions) => BackendSession
  deskagentHome?: null | string
  electronNet: { fetch: FetchFunction }
  rebuildTrayMenu?: () => void
  rememberLog: (chunk: string) => void
  resetBackendCache?: () => void
  resolveDeskAgentVersion: () => string
  safeStorage?: null | SafeStorageApi
}

export function ensureBackendSession(deps: AuthIpcDeps): BackendSession {
  if (deps.backendSession) {
    return deps.backendSession
  }

  deps.backendSession = deps.createBackendSession({
    appVersion: deps.resolveDeskAgentVersion(),
    defaultBaseUrl: resolveBackendUrl(deps.deskagentHome),
    fetchImpl: (url: string, options?: RequestInit) => deps.electronNet.fetch(url, options),
    log: (chunk: string) => deps.rememberLog(chunk),
    safeStorage: deps.safeStorage,
    userDataDir: deps.app.getPath('userData')
  })

  deps.backendSession
    .restoreSession()
    .then((snapshot: null | SessionSnapshot) => {
      if (snapshot) {
        deps.rebuildTrayMenu?.()
        deps.broadcastAuthChanged?.(snapshot)
      }
    })
    .catch((error: Error) => {
      deps.rememberLog(`[session] restore failed: ${error.message}`)
    })

  return deps.backendSession
}

export function registerAuthIpc({ deps, ipcMain }: { deps: AuthIpcDeps; ipcMain: IpcMain }): void {
  ipcMain.handle('deskagent:auth:activate', async (_event, payload: DesktopActivatePayload) => {
    const session = ensureBackendSession(deps)
    const built = deps.buildClientContext?.() ?? {}

    const enriched = {
      ...(payload || {}),
      clientContext: (payload && payload.clientContext) || built.client_context || null
    }

    const result = await session.activate(enriched)
    deps.resetBackendCache?.()
    deps.rebuildTrayMenu?.()
    deps.broadcastAuthChanged?.(session.getSession())

    if (result && result.baseUrl) {
      writeStoredBackendUrl(deps.deskagentHome, result.baseUrl)
    }

    return result
  })

  ipcMain.handle('deskagent:auth:refresh', async (_event, payload?: { clientContext?: unknown }) => {
    const session = ensureBackendSession(deps)
    const built = deps.buildClientContext?.() ?? {}

    const enriched = {
      ...(payload || {}),
      clientContext: (payload && payload.clientContext) || built.client_context || null
    }

    const result = await session.refresh(enriched)
    deps.resetBackendCache?.()
    deps.broadcastAuthChanged?.(session.getSession())

    return result
  })

  ipcMain.handle('deskagent:auth:logout', async () => {
    const session = ensureBackendSession(deps)
    const result = await session.logout()
    deps.resetBackendCache?.()
    deps.rebuildTrayMenu?.()
    deps.broadcastAuthChanged?.(session.getSession())

    return result
  })

  ipcMain.handle('deskagent:auth:get-session', async () => {
    const session = ensureBackendSession(deps)

    return session.getSession()
  })

  ipcMain.handle('deskagent:auth:get-default-backend-url', async () => {
    return resolveBackendUrl(deps.deskagentHome)
  })
}
