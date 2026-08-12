'use strict'

const { resolveBackendUrl, writeStoredBackendUrl } = require('../shared/config.cjs')

function ensureBackendSession(deps) {
  if (deps.backendSession) return deps.backendSession
  deps.backendSession = deps.createBackendSession({
    userDataDir: deps.app.getPath('userData'),
    safeStorage: deps.safeStorage,
    appVersion: deps.resolveDeskAgentVersion(),
    fetchImpl: (url, options) => deps.electronNet.fetch(url, options),
    defaultBaseUrl: resolveBackendUrl(deps.deskagentHome),
    log: chunk => deps.rememberLog(chunk)
  })
  // restoreSession() is now async — it calls /api/user/activate to obtain a
  // fresh session JWT from the stored activation code.  Kick it off in the
  // background; the session object is returned immediately (without a JWT
  // until activation completes).  When restore resolves, broadcast the auth
  // change so both renderer windows flip to authenticated.
  deps.backendSession
    .restoreSession()
    .then(snapshot => {
      if (snapshot) {
        deps.rebuildTrayMenu?.()
        deps.broadcastAuthChanged?.(snapshot)
      }
    })
    .catch(error => {
      deps.rememberLog(`[session] restore failed: ${error.message}`)
    })
  return deps.backendSession
}

function registerAuthIpc({ ipcMain, deps }) {
  ipcMain.handle('deskagent:auth:activate', async (_event, payload) => {
    const session = ensureBackendSession(deps)
    // Inject clientContext here (not in the renderer): only main process has
    // access to process.platform / arch / release and to $DESKAGENT_HOME/skills.
    // Backend runs in a cloud container and cannot reach the host OS, so this
    // payload is the only signal it gets about the local environment.
    const built = deps.buildClientContext?.() ?? {}
    const enriched = {
      ...(payload || {}),
      clientContext: (payload && payload.clientContext) || built.client_context || null
    }
    const result = await session.activate(enriched)
    // JWT changed — invalidate the cached backend connection so the next
    // ensureBackend() re-resolves with the fresh token.
    deps.resetBackendCache?.()
    deps.rebuildTrayMenu?.()
    // Sync the sprite window's per-renderer $auth so it boots its gateway.
    deps.broadcastAuthChanged?.(session.getSession())
    // Persist the URL the user just activated with as the next-launch
    // default. Logout intentionally does NOT clear this file.
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
    // Tell the sprite window to tear down its gateway and return to the egg.
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

module.exports = { registerAuthIpc, ensureBackendSession }
