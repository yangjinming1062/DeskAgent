/**
 * Owns the desktop's session with the cloud Backend: base URL, JWT, user
 * info, expires_at. Persists to disk with JWT encrypted via safeStorage.
 *
 * Pure (no electron require at the top) — caller injects electron modules
 * via the constructor for unit-testability.
 */

const fs = require('node:fs')
const path = require('node:path')

const { createBackendClient, BackendRequestError } = require('./client.cjs')

const SESSION_FILENAME = 'agent-session.json'
const SESSION_SCHEMA_VERSION = 1
// Same lifetime the backend configures for access_token_expire_minutes.
const KNOWN_TOKEN_TTL_MS = 8 * 60 * 60 * 1000
// Proactive refresh fires this many ms before tokenExpiresAt.
const REFRESH_LEAD_MS = 5 * 60 * 1000
// Model-config cache TTL. The renderer reads this for the Settings → Account
// block; both paths funnel through `getModelConfig()` so they share one
// network round trip.
const MODEL_CONFIG_CACHE_TTL_MS = 5 * 60 * 1000

class SessionError extends Error {
  constructor({ code, message, cause, status }) {
    super(message)
    this.name = 'SessionError'
    this.code = code
    if (cause) this.cause = cause
    if (status !== undefined) this.status = status
  }
}

// Sync wrapper over utils.cjs::atomicWriteFile. Single source of truth for
// the write-tmp-then-rename pattern lives in utils.cjs; the JSON.stringify
// lives here. Caller is persistCurrent() in this same file, called
// synchronously on every login/refresh/restore — keep the sync signature.
function atomicWriteJson(targetPath, payload) {
  // atomicWriteFile is async; for the synchronous persist path we replicate
  // its semantics inline (unique tmp + explicit unlink on failure).
  const tmp = `${targetPath}.${process.pid}.${Date.now()}.tmp`
  fs.mkdirSync(path.dirname(targetPath), { recursive: true })
  try {
    fs.writeFileSync(tmp, JSON.stringify(payload, null, 2), 'utf8')
    fs.renameSync(tmp, targetPath)
  } catch (error) {
    try {
      fs.unlinkSync(tmp)
    } catch {
      /* best-effort cleanup */
    }
    throw error
  }
}

function readJsonSafe(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'))
  } catch {
    return null
  }
}

function encryptToken(raw, safeStorage) {
  const value = String(raw || '')
  if (!value) return null

  if (!safeStorage?.isEncryptionAvailable?.()) {
    throw new SessionError({
      code: 'safe-storage-unavailable',
      message:
        'Secure token storage is unavailable, so DeskAgent Desktop cannot save the Backend token. ' +
        'Enable OS keychain access and try again.'
    })
  }

  return {
    encoding: 'safeStorage',
    value: safeStorage.encryptString(value).toString('base64')
  }
}

function decryptToken(blob, safeStorage) {
  if (!blob || typeof blob !== 'object') return null
  if (blob.encoding !== 'safeStorage') return null
  if (!safeStorage?.isEncryptionAvailable?.()) return null

  try {
    const buf = Buffer.from(String(blob.value || ''), 'base64')
    return safeStorage.decryptString(buf)
  } catch {
    return null
  }
}

function normalizeUser(raw) {
  if (!raw || typeof raw !== 'object') return null
  const id = raw.id ?? raw.user_id ?? null
  const username = raw.username ?? null
  if (id === null && username === null) return null
  return {
    id: id === null ? null : Number(id),
    username: username === null ? null : String(username)
  }
}

function createBackendSession(options = {}) {
  const {
    userDataDir,
    safeStorage,
    appVersion = 'unknown',
    fetchImpl,
    now = () => Date.now(),
    defaultBaseUrl = null
  } = options

  if (!userDataDir) {
    throw new SessionError({ code: 'missing-user-data-dir', message: 'userDataDir is required' })
  }
  if (typeof fetchImpl !== 'function') {
    throw new SessionError({ code: 'missing-fetch', message: 'fetch implementation is required' })
  }

  // Backend has no per-device binding; identity is enforced by JWT jti revocation alone.

  const sessionPath = path.join(userDataDir, SESSION_FILENAME)
  const log = typeof options.log === 'function' ? options.log : () => {}

  let cached = null // { baseUrl, token, tokenExpiresAt, user, encryptedToken }
  let backendClient = null
  let backendClientBaseUrl = null
  let loginPromise = null
  let cachedModelConfig = null // { value, expiresAt } — shared across renderer reads
  let refreshTimer = null // proactive token refresh timer

  function persistCurrent() {
    if (!cached) return
    atomicWriteJson(sessionPath, {
      schemaVersion: SESSION_SCHEMA_VERSION,
      baseUrl: cached.baseUrl,
      token: cached.encryptedToken,
      tokenExpiresAt: cached.tokenExpiresAt,
      user: cached.user,
      savedAt: now()
    })
  }

  function clearRefreshTimer() {
    if (refreshTimer !== null) {
      clearTimeout(refreshTimer)
      refreshTimer = null
    }
  }

  // Schedule an automatic token refresh before expiry. On failure the timer
  // is simply cleared — the token will expire naturally and the next WS
  // reconnect (close code 1008) or REST 401 will trigger the session-expired
  // flow.
  function scheduleRefresh() {
    clearRefreshTimer()
    if (!cached?.tokenExpiresAt) return

    const delay = cached.tokenExpiresAt - now() - REFRESH_LEAD_MS
    if (delay <= 0) {
      // Token already expired or about to — don't schedule.
      return
    }

    refreshTimer = setTimeout(() => {
      refreshTimer = null
      if (!cached?.token) return
      log('[session] proactive token refresh triggered')
      refresh().catch(err => {
        log(`[session] proactive refresh failed: ${err?.message || err}`)
      })
    }, delay)
    // Don't keep the event loop alive just to fire a refresh — the
    // session is also reloaded on next ensureBackend()/restoreSession(),
    // and on quit we don't want a pending refresh to delay shutdown.
    if (typeof refreshTimer.unref === 'function') refreshTimer.unref()
  }

  function loadFromDisk() {
    const raw = readJsonSafe(sessionPath)
    if (!raw || typeof raw !== 'object') return null
    if (raw.schemaVersion !== SESSION_SCHEMA_VERSION) return null

    const token = decryptToken(raw.token, safeStorage)
    if (!token) {
      // Disk blob present but decryption failed (keychain rotated, profile
      // switched). Drop silently — the next login will mint a fresh one.
      return null
    }

    return {
      baseUrl: typeof raw.baseUrl === 'string' ? raw.baseUrl : null,
      token,
      encryptedToken: raw.token,
      tokenExpiresAt: Number.isFinite(raw.tokenExpiresAt) && raw.tokenExpiresAt > 0 ? raw.tokenExpiresAt : null,
      user: normalizeUser(raw.user),
      savedAt: Number.isFinite(raw.savedAt) ? raw.savedAt : null
    }
  }

  function snapshot() {
    if (!cached) return null
    const { baseUrl, token, tokenExpiresAt, user } = cached
    return { baseUrl, tokenExpiresAt, user, hasToken: Boolean(token) }
  }

  function effectiveBaseUrl() {
    if (cached?.baseUrl) return cached.baseUrl
    if (defaultBaseUrl) return defaultBaseUrl
    return null
  }

  function client() {
    const baseUrl = effectiveBaseUrl()
    if (!baseUrl) {
      throw new SessionError({
        code: 'no-base-url',
        message: 'Backend base URL is not configured. Check $DESKAGENT_HOME/desktop-config.json or sign in.'
      })
    }
    if (backendClient && backendClientBaseUrl === baseUrl) return backendClient
    backendClient = createBackendClient({ baseUrl, fetch: fetchImpl })
    backendClientBaseUrl = baseUrl
    return backendClient
  }

  // Translates BackendRequestError → SessionError for IPC handlers.
  function translateBackendError(error) {
    if (!(error instanceof BackendRequestError)) throw error
    if (error.status === 401) {
      throw new SessionError({
        code: 'bad-credentials',
        status: 401,
        message: '用户名或密码错误。',
        cause: error
      })
    }
    throw new SessionError({
      code: error.code || 'backend-error',
      status: error.status,
      message: error.message,
      cause: error
    })
  }

  // Single mutation site for "we now have a session". `login`, `refresh`,
  // and `adoptSession` all funnel through here so the invalidate-/
  // persist-/-schedule-refresh invariants stay in lockstep — future
  // session-wide side effects (revocation, telemetry, model-config reset)
  // belong in this function, not in each caller.
  function applySession({ baseUrl, token, tokenExpiresAt, user, source }) {
    if (!token) {
      throw new SessionError({
        code: 'no-token',
        message: 'Cannot apply a session without a token.'
      })
    }
    const resolvedBaseUrl = baseUrl || cached?.baseUrl || null
    const resolvedUser = normalizeUser(user) || cached?.user || { id: null, username: null }
    const encryptedToken = encryptToken(token, safeStorage)

    cached = {
      baseUrl: resolvedBaseUrl,
      token,
      tokenExpiresAt,
      user: resolvedUser,
      encryptedToken
    }

    // Invalidate the cached BackendClient so the next request hits the
    // freshly applied baseUrl. Also drop the model-config cache — the
    // new session may belong to a different user with a different config.
    backendClient = null
    backendClientBaseUrl = null
    cachedModelConfig = null

    persistCurrent()
    scheduleRefresh()

    log(`[session] ${source} ok base=${resolvedBaseUrl} user=${resolvedUser?.username ?? '?'}`)
    return snapshot()
  }

  function login(payload = {}) {
    const { username, password, baseUrl: overrideBaseUrl, clientContext } = payload
    if (!username || !password) {
      throw new SessionError({
        code: 'missing-credentials',
        message: 'Username and password are required.'
      })
    }

    const baseUrl = overrideBaseUrl || effectiveBaseUrl()
    if (!baseUrl) {
      throw new SessionError({
        code: 'no-base-url',
        message: 'Backend base URL is not configured.'
      })
    }

    if (loginPromise) return loginPromise

    const backend = createBackendClient({ baseUrl, fetch: fetchImpl })
    loginPromise = backend
      .post('/api/user/login', {
        body: {
          username,
          password,
          client_version: appVersion,
          client_context: clientContext || undefined
        }
      })
      .then(response => {
        if (!response || typeof response.access_token !== 'string' || !response.access_token) {
          throw new SessionError({
            code: 'invalid-login-response',
            message: 'Backend did not return an access token.'
          })
        }

        const expiresIn =
          Number.isFinite(response.expires_in) && response.expires_in > 0
            ? response.expires_in * 1000
            : KNOWN_TOKEN_TTL_MS
        return applySession({
          baseUrl,
          token: response.access_token,
          tokenExpiresAt: now() + expiresIn,
          user: response.user,
          source: 'login'
        })
      })
      .catch(translateBackendError)
      .finally(() => {
        loginPromise = null
      })

    return loginPromise
  }

  function refresh(payload = {}) {
    if (!cached || !cached.token) {
      return Promise.reject(
        new SessionError({
          code: 'not-logged-in',
          message: 'Cannot refresh without an active session.'
        })
      )
    }

    const { clientContext } = payload
    const backend = client()

    return backend
      .post('/api/user/refresh', {
        body: {
          client_version: appVersion,
          client_context: clientContext || undefined
        },
        token: cached.token
      })
      .then(response => {
        if (!response || typeof response.access_token !== 'string' || !response.access_token) {
          throw new SessionError({
            code: 'invalid-refresh-response',
            message: 'Backend did not return an access token.'
          })
        }

        const expiresIn =
          Number.isFinite(response.expires_in) && response.expires_in > 0
            ? response.expires_in * 1000
            : KNOWN_TOKEN_TTL_MS
        return applySession({
          token: response.access_token,
          tokenExpiresAt: now() + expiresIn,
          user: response.user,
          source: 'refresh'
        })
      })
      .catch(translateBackendError)
  }

  function logout() {
    if (!cached?.token) {
      clearSession()
      return Promise.resolve({ ok: true })
    }

    const backend = client()
    return backend
      .post('/api/user/logout', { token: cached.token })
      .then(() => {
        clearSession()
        log('[session] logout ok')
        return { ok: true }
      })
      .catch(error => {
        // Network failure on logout: local token still needs to die.
        log(`[session] logout backend call failed: ${error?.message || error}`)
        clearSession()
        return { ok: true, backendUnreachable: true, error: error?.message || String(error) }
      })
  }

  function clearSession() {
    clearRefreshTimer()
    cached = null
    backendClient = null
    backendClientBaseUrl = null
    cachedModelConfig = null
    try {
      fs.unlinkSync(sessionPath)
    } catch (error) {
      if (error?.code !== 'ENOENT') {
        log(`[session] clearSession unlink failed: ${error.message}`)
      }
    }
  }

  function changePassword(payload = {}) {
    const { current_password, new_password } = payload
    if (!current_password || !new_password) {
      throw new SessionError({
        code: 'missing-passwords',
        message: 'Both current_password and new_password are required.'
      })
    }
    if (!cached?.token) {
      throw new SessionError({
        code: 'no-session',
        message: 'Not signed in.'
      })
    }

    return client()
      .post('/api/user/change-password', {
        body: { current_password, new_password },
        headers: authHeaders()
      })
      .then(response => {
        const message =
          response && typeof response === 'object' && typeof response.message === 'string'
            ? response.message
            : 'Password updated.'
        log('[session] change-password ok')
        return { ok: true, message }
      })
      .catch(translateBackendError)
  }

  function getModelConfig({ force = false } = {}) {
    if (!cached?.token) {
      throw new SessionError({
        code: 'no-session',
        message: 'Not signed in.'
      })
    }

    const nowMs = now()
    if (!force && cachedModelConfig && cachedModelConfig.expiresAt > nowMs) {
      return Promise.resolve(cachedModelConfig.value)
    }

    return client()
      .get('/api/user/model-config', { headers: authHeaders() })
      .then(value => {
        cachedModelConfig = {
          value,
          expiresAt: nowMs + MODEL_CONFIG_CACHE_TTL_MS
        }
        return value
      })
      .catch(translateBackendError)
  }

  function restoreSession() {
    const loaded = loadFromDisk()
    if (!loaded) return null
    cached = {
      baseUrl: loaded.baseUrl,
      token: loaded.token,
      tokenExpiresAt: loaded.tokenExpiresAt,
      user: loaded.user
    }
    scheduleRefresh()
    log(`[session] restored from disk base=${loaded.baseUrl} user=${loaded.user?.username ?? '?'}`)
    return snapshot()
  }

  // Adopt an externally-validated session (currently the installer
  // bootstrap file). Funnels through applySession so it stays in lockstep
  // with login()/refresh() — future session-wide side effects only need
  // to be added in one place.
  function adoptSession({ baseUrl, token, tokenExpiresAt, user }) {
    const expiresAt =
      Number.isFinite(tokenExpiresAt) && tokenExpiresAt > 0
        ? tokenExpiresAt
        : now() + KNOWN_TOKEN_TTL_MS

    return applySession({
      baseUrl,
      token,
      tokenExpiresAt: expiresAt,
      user,
      source: 'adopted'
    })
  }

  function getSession() {
    return snapshot()
  }

  function getToken() {
    return cached?.token ?? null
  }

  function authHeaders() {
    if (!cached?.token) return {}
    return { Authorization: `Bearer ${cached.token}` }
  }

  return {
    login,
    refresh,
    logout,
    changePassword,
    restoreSession,
    adoptSession,
    clearSession,
    getSession,
    getToken,
    getModelConfig,
    authHeaders,
    _sessionPath: sessionPath
  }
}

module.exports = {
  createBackendSession,
  SessionError,
  encryptToken,
  decryptToken,
  SESSION_FILENAME,
  SESSION_SCHEMA_VERSION,
  KNOWN_TOKEN_TTL_MS,
  MODEL_CONFIG_CACHE_TTL_MS
}
