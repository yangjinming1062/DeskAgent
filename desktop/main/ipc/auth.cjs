'use strict'

// Backend session IPC: login / logout / session snapshot.

const { getBackendUrl } = require('../shared/config.cjs')

function ensureBackendSession(deps) {
  if (deps.backendSession) return deps.backendSession
  deps.backendSession = deps.createBackendSession({
    userDataDir: deps.app.getPath('userData'),
    safeStorage: deps.safeStorage,
    appVersion: deps.resolveDeskAgentVersion(),
    fetchImpl: (url, options) => deps.electronNet.fetch(url, options),
    defaultBaseUrl: getBackendUrl() || null,
    log: chunk => deps.rememberLog(chunk)
  })
  // Best-effort restore; failure routes user to login screen.
  try {
    deps.backendSession.restoreSession()
  } catch (error) {
    deps.rememberLog(`[session] restore failed: ${error.message}`)
  }
  // A restored session flips auth state before any login IPC fires — rebuild
  // the tray menu so it shows the authenticated items right after startup.
  deps.rebuildTrayMenu?.()
  return deps.backendSession
}

function registerAuthIpc({ ipcMain, deps }) {
  ipcMain.handle('deskagent:auth:login', async (_event, payload) => {
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
    const result = await session.login(enriched)
    // JWT changed — invalidate the cached backend connection so the next
    // ensureBackend() re-resolves with the fresh token.
    deps.resetBackendCache?.()
    deps.rebuildTrayMenu?.()
    // Sync the sprite window's per-renderer $auth so it boots its gateway.
    deps.broadcastAuthChanged?.(session.getSession())
    // The sprite takes over; dismiss the login form.
    deps.hideToolWindow?.()
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
    // Surface the login form again so the user can re-authenticate.
    deps.showToolWindow?.()
    return result
  })

  ipcMain.handle('deskagent:auth:get-session', async () => {
    const session = ensureBackendSession(deps)
    return session.getSession()
  })

  ipcMain.handle('deskagent:auth:change-password', async (_event, payload) => {
    const session = ensureBackendSession(deps)
    return session.changePassword(payload || {})
  })

  ipcMain.handle('deskagent:model-config:get', async () => {
    const session = ensureBackendSession(deps)
    const full = await session.getModelConfig()
    return {
      llm_model_name: full?.llm_model_name ?? '',
      llm_base_url: full?.llm_base_url ?? '',
      llm_api_key_fingerprint: full?.llm_api_key_fingerprint ?? '',
      llm_api_key_set: Boolean(full?.llm_api_key_set)
    }
  })
}

module.exports = { registerAuthIpc, ensureBackendSession }
