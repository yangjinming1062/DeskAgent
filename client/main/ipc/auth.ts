import type { IpcMain } from 'electron'

import { resolveBackendUrl, writeStoredBackendUrl } from '../shared/config'

export interface AuthIpcDeps {
  app: { getPath: (name: string) => string }
  backendSession?: any
  broadcastAuthChanged?: (session: any) => void
  buildClientContext?: () => any
  createBackendSession: (options: any) => any
  deskagentHome?: null | string
  electronNet: { fetch: (url: string, init?: any) => Promise<any> }
  rebuildTrayMenu?: () => void
  rememberLog: (chunk: string) => void
  resetBackendCache?: () => void
  resolveDeskAgentVersion: () => string
  safeStorage?: any
}

export function ensureBackendSession(deps: AuthIpcDeps): any {
  if (deps.backendSession) {
    return deps.backendSession
  }

  deps.backendSession = deps.createBackendSession({
    appVersion: deps.resolveDeskAgentVersion(),
    defaultBaseUrl: resolveBackendUrl(deps.deskagentHome),
    fetchImpl: (url: string, options: any) => deps.electronNet.fetch(url, options),
    log: (chunk: string) => deps.rememberLog(chunk),
    safeStorage: deps.safeStorage,
    userDataDir: deps.app.getPath('userData')
  })

  deps.backendSession
    .restoreSession()
    .then((snapshot: any) => {
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
  ipcMain.handle('deskagent:auth:activate', async (_event, payload) => {
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

  ipcMain.handle('deskagent:auth:refresh', async (_event, payload) => {
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
