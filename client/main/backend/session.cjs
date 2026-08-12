const fs = require('node:fs')
const path = require('node:path')

const { createBackendClient, BackendRequestError } = require('./client.cjs')

/**
 * Owns the desktop's session with the cloud Backend.
 *
 * Activation-token model: the activation code (base64-encoded
 * ``{b, t}`` JSON) is the persistent credential, encrypted via
 * safeStorage and written to ``agent-session.json``.  The session
 * JWT returned by ``/api/user/activate`` lives in memory only and
 * is proactively refreshed before expiry — same lifecycle as before.
 */

const SESSION_FILENAME = 'agent-session.json'
const SESSION_SCHEMA_VERSION = 2
// Same lifetime the backend configures for access_token_expire_minutes.
const KNOWN_TOKEN_TTL_MS = 8 * 60 * 60 * 1000
// Proactive refresh fires this many ms before tokenExpiresAt.
const REFRESH_LEAD_MS = 5 * 60 * 1000

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
// synchronously on every activate/refresh/restore — keep the sync signature.
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

/**
 * Decode a base64url activation code into ``{ baseUrl, token }``.
 * The code encodes ``{"b":"<baseUrl>","t":"<token>"}`` as compact JSON.
 */
function decodeActivationCode(code) {
  const padding = '='.repeat((4 - (code.length % 4)) % 4)
  const raw = Buffer.from(code + padding, 'base64url').toString('utf8')
  const data = JSON.parse(raw)
  const baseUrl = data.b
  const token = data.t
  if (!baseUrl || !token) throw new Error('activation code missing required fields')
  return { baseUrl, token }
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

  const sessionPath = path.join(userDataDir, SESSION_FILENAME)
  const log = typeof options.log === 'function' ? options.log : () => {}

  // cached shape:
  //   { baseUrl, activationCode,
  //     token (session JWT, in-memory), tokenExpiresAt, user }
  let cached = null
  let backendClient = null
  let backendClientBaseUrl = null
  let activatePromise = null
  let refreshTimer = null // proactive token refresh timer

  function persistCurrent() {
    if (!cached) return
    atomicWriteJson(sessionPath, {
      schemaVersion: SESSION_SCHEMA_VERSION,
      baseUrl: cached.baseUrl,
      activationCode: encryptToken(cached.activationCode, safeStorage),
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

    const activationCode = decryptToken(raw.activationCode, safeStorage)
    if (!activationCode) {
      // Disk blob present but decryption failed (keychain rotated, profile
      // switched). Drop silently — the next activation will mint a fresh one.
      return null
    }

    return {
      baseUrl: typeof raw.baseUrl === 'string' ? raw.baseUrl : null,
      activationCode,
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
        message: 'Backend base URL is not configured. Activate with a valid activation code.'
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
        message: '激活码无效。',
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

  // Single mutation site for "we now have a session". `activate` and
  // `refresh` both funnel through here so the invalidate-/persist-/
  // schedule-refresh invariants stay in lockstep.
  //
  // `activationCode` is the persistent credential (encrypted, written to
  // disk).  `token` is the ephemeral session JWT (in-memory only).
  // When `activationCode` is omitted (e.g. from refresh()), the previously
  // stored one is reused.
  function applySession({ baseUrl, activationCode, token, tokenExpiresAt, user, source }) {
    if (!token) {
      throw new SessionError({
        code: 'no-token',
        message: 'Cannot apply a session without a session token.'
      })
    }
    const resolvedBaseUrl = baseUrl || cached?.baseUrl || null
    const resolvedUser = normalizeUser(user) || cached?.user || { id: null, username: null }

    // Activation code: persistent credential. When not provided (e.g. refresh
    // path), reuse the one already in cached.
    const resolvedCode = activationCode || cached?.activationCode || null
    if (!resolvedCode) {
      throw new SessionError({
        code: 'no-activation-code',
        message: 'Cannot apply a session without an activation code.'
      })
    }

    cached = {
      baseUrl: resolvedBaseUrl,
      activationCode: resolvedCode,
      token,
      tokenExpiresAt,
      user: resolvedUser
    }

    // Invalidate the cached BackendClient so the next request hits the
    // freshly applied baseUrl.
    backendClient = null
    backendClientBaseUrl = null

    persistCurrent()
    scheduleRefresh()

    log(`[session] ${source} ok base=${resolvedBaseUrl} user=${resolvedUser?.username ?? '?'}`)
    return snapshot()
  }

  function activate(payload = {}) {
    const { code, clientContext } = payload
    if (!code) {
      throw new SessionError({
        code: 'missing-code',
        message: 'Activation code is required.'
      })
    }

    // Decode the activation code to learn the backend address (needed to
    // know which backend to call).  The code itself is sent to the backend
    // verbatim — the backend decodes it again to extract the token.
    let baseUrl
    try {
      const decoded = decodeActivationCode(code)
      baseUrl = decoded.baseUrl
    } catch {
      throw new SessionError({
        code: 'invalid-code',
        message: '激活码格式无效。'
      })
    }

    if (!baseUrl) {
      throw new SessionError({
        code: 'no-base-url',
        message: 'Activation code does not contain a backend address.'
      })
    }

    if (activatePromise) return activatePromise

    const backend = createBackendClient({ baseUrl, fetch: fetchImpl })
    activatePromise = backend
      .post('/api/user/activate', {
        body: {
          code,
          client_version: appVersion,
          client_context: clientContext || undefined
        }
      })
      .then(response => {
        if (!response || typeof response.access_token !== 'string' || !response.access_token) {
          throw new SessionError({
            code: 'invalid-activate-response',
            message: 'Backend did not return an access token.'
          })
        }

        const expiresIn =
          Number.isFinite(response.expires_in) && response.expires_in > 0
            ? response.expires_in * 1000
            : KNOWN_TOKEN_TTL_MS
        return applySession({
          baseUrl,
          activationCode: code,
          token: response.access_token,
          tokenExpiresAt: now() + expiresIn,
          user: response.user,
          source: 'activate'
        })
      })
      .catch(translateBackendError)
      .finally(() => {
        activatePromise = null
      })

    return activatePromise
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
    try {
      fs.unlinkSync(sessionPath)
    } catch (error) {
      if (error?.code !== 'ENOENT') {
        log(`[session] clearSession unlink failed: ${error.message}`)
      }
    }
  }

  async function restoreSession() {
    const loaded = loadFromDisk()
    if (!loaded) return null

    // Seed cached with the activation code so applySession() can reuse it
    // without re-receiving it from activate().
    cached = {
      baseUrl: loaded.baseUrl,
      activationCode: loaded.activationCode,
      token: null, // no session JWT yet — activate() will obtain one
      tokenExpiresAt: null,
      user: loaded.user
    }

    try {
      // Delegate to activate() — it decodes the code (redundant but trivial),
      // posts to /api/user/activate, validates the response, and funnels
      // through applySession(). This avoids duplicating the HTTP pipeline.
      return await activate({ code: loaded.activationCode })
    } catch (err) {
      // Transient failure (network down, backend 500): preserve the
      // activation code on disk so the next launch can retry. Only a
      // definitive "code invalid" (401) should warrant clearing it.
      if (err instanceof SessionError && err.code === 'bad-credentials') {
        clearSession()
      }
      log(`[session] restore activation failed: ${err?.message || err}`)
      return null
    }
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
    activate,
    refresh,
    logout,
    restoreSession,
    clearSession,
    getSession,
    getToken,
    authHeaders,
    _sessionPath: sessionPath
  }
}

module.exports = {
  createBackendSession,
  SessionError,
  encryptToken,
  decryptToken,
  decodeActivationCode,
  SESSION_FILENAME,
  SESSION_SCHEMA_VERSION,
  KNOWN_TOKEN_TTL_MS
}
