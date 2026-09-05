import { type DesktopActivatePayload, IPC } from '@ipc/contracts'
import type { IpcMain } from 'electron'

import type { FetchFunction } from '../backend/client'
import type { BackendSession, BackendSessionOptions, SessionSnapshot } from '../backend/session'
import type { SafeStorageApi } from '../security/hardening'
import { readStoredBackendUrl, writeStoredBackendUrl } from '../shared/config'

interface AuthIpcDeps {
  app: { getPath: (name: string) => string }
  autoStopBridge?: () => void
  backendSession?: null | BackendSession
  broadcastAuthChanged?: (session: null | SessionSnapshot) => void
  buildClientContext?: () => { client_context?: unknown }
  createBackendSession: (options: BackendSessionOptions) => BackendSession
  spiritagentHome?: null | string
  electronNet: { fetch: FetchFunction }
  rebuildTrayMenu?: () => void
  rememberLog: (chunk: string) => void
  resetBackendCache?: () => void
  resolveSpiritAgentVersion: () => string
  safeStorage?: null | SafeStorageApi
}

export function ensureBackendSession(deps: AuthIpcDeps): BackendSession {
  if (deps.backendSession) {
    return deps.backendSession
  }

  deps.backendSession = deps.createBackendSession({
    appVersion: deps.resolveSpiritAgentVersion(),
    defaultBaseUrl: readStoredBackendUrl(deps.spiritagentHome),
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
  ipcMain.handle(IPC.invoke.authActivate, async (_event, payload: DesktopActivatePayload) => {
    const session = ensureBackendSession(deps)
    const built = deps.buildClientContext?.() ?? {}

    const enriched = {
      ...(payload || {}),
      clientContext: built.client_context || null
    }

    const result = await session.activate(enriched)
    deps.resetBackendCache?.()
    deps.rebuildTrayMenu?.()
    deps.broadcastAuthChanged?.(session.getSession())

    if (result && result.baseUrl) {
      await writeStoredBackendUrl(deps.spiritagentHome, result.baseUrl)
    }

    return result
  })

  ipcMain.handle(IPC.invoke.authRefresh, async () => {
    const session = ensureBackendSession(deps)
    const built = deps.buildClientContext?.() ?? {}
    const enriched = { clientContext: built.client_context || null }

    const result = await session.refresh(enriched)
    deps.resetBackendCache?.()
    deps.broadcastAuthChanged?.(session.getSession())

    return result
  })

  ipcMain.handle(IPC.invoke.authLogout, async () => {
    const session = ensureBackendSession(deps)
    const result = await session.logout()
    deps.resetBackendCache?.()
    deps.rebuildTrayMenu?.()
    deps.broadcastAuthChanged?.(session.getSession())
    deps.autoStopBridge?.()

    return result
  })

  ipcMain.handle(IPC.invoke.authGetSession, async () => {
    const session = ensureBackendSession(deps)

    return session.getSession()
  })
}
