'use strict'

// One-shot installer → desktop session handoff.
//
// The installer writes `agent-session-bootstrap.json` under the canonical
// $DESKAGENT_HOME after a successful POST /api/user/login (see
// installer/src-tauri/src/bootstrap_session.rs). Desktop's main process
// reads the file at startup, validates the token against the backend by
// calling POST /api/user/refresh, then hands the token to the normal
// BackendSession so it gets encrypted through Electron safeStorage. The
// bootstrap file is atomically renamed to `.consumed` so a second launch
// never replays it.
//
// The path can be overridden via DESKAGENT_DESKTOP_BOOTSTRAP_SESSION,
// and `consumeBootstrapSession` is the single entry point wired into
// entry.cjs. Returns the validated session snapshot on success, or
// `null` when there's nothing to consume (or the file is invalid).
//
// Cross-language constants (FILENAME / SCHEMA_VERSION / CONSUMED_SUFFIX)
// are canonical on the Rust side at `installer/src-tauri/src/paths.rs`.
// The values below are a deliberate mirror, enforced at test time by
// `bootstrap-session.test.cjs::bootstrap_constants_match_rust_paths` —
// don't bump one without the other.

const fs = require('node:fs')
const path = require('node:path')

// MUST match installer/src-tauri/src/paths.rs BOOTSTRAP_* constants.
const SCHEMA_VERSION = 1
const FILENAME = 'agent-session-bootstrap.json'
const CONSUMED_SUFFIX = '.consumed'
const REFRESH_TIMEOUT_MS = 15_000

const BOOTSTRAP_ENV_VAR = 'DESKAGENT_DESKTOP_BOOTSTRAP_SESSION'

function defaultBootstrapPath(deskagentHome) {
  return path.join(deskagentHome, FILENAME)
}

function readBootstrapFile(filePath) {
  let raw
  try {
    raw = fs.readFileSync(filePath, 'utf8')
  } catch (error) {
    return { ok: false, code: error?.code === 'ENOENT' ? 'missing' : 'unreadable', cause: error }
  }

  let parsed
  try {
    parsed = JSON.parse(raw)
  } catch (error) {
    return { ok: false, code: 'malformed', cause: error }
  }

  if (!parsed || typeof parsed !== 'object') {
    return { ok: false, code: 'malformed' }
  }
  if (parsed.schemaVersion !== SCHEMA_VERSION) {
    return { ok: false, code: 'schema-mismatch' }
  }
  if (typeof parsed.baseUrl !== 'string' || !parsed.baseUrl) {
    return { ok: false, code: 'missing-base-url' }
  }
  if (typeof parsed.token !== 'string' || !parsed.token) {
    return { ok: false, code: 'missing-token' }
  }
  if (
    typeof parsed.tokenExpiresAt !== 'number' ||
    !Number.isFinite(parsed.tokenExpiresAt)
  ) {
    return { ok: false, code: 'missing-expiry' }
  }

  return {
    ok: true,
    session: {
      baseUrl: parsed.baseUrl,
      token: parsed.token,
      tokenExpiresAt: parsed.tokenExpiresAt,
      user:
        parsed.user && typeof parsed.user === 'object'
          ? { id: parsed.user.id ?? null, username: parsed.user.username ?? null }
          : { id: null, username: null },
      savedAt: Number.isFinite(parsed.savedAt) ? parsed.savedAt : null
    }
  }
}

// Rename the bootstrap file to `.consumed` before validating so a half-applied
// handoff can't replay on the next launch. Atomic on POSIX (rename within
// the same filesystem). On Windows a same-volume rename is atomic when the
// file isn't open and DACLs don't differ — `write_user_only()` in the Rust
// writer (`installer/src-tauri/src/bootstrap_session.rs`) creates the file
// fresh and never opens it for read in parallel, so we don't hit the
// "rename collision" case. Returns the new path, or null if the file was
// already gone (caller treats as a benign miss).
function renameToConsumed(filePath) {
  const consumedPath = `${filePath}${CONSUMED_SUFFIX}`
  try {
    fs.renameSync(filePath, consumedPath)
    return consumedPath
  } catch (error) {
    if (error?.code === 'ENOENT') return null
    throw error
  }
}

function deleteBootstrapFile(filePath) {
  try {
    fs.unlinkSync(filePath)
  } catch (error) {
    if (error?.code !== 'ENOENT') throw error
  }
}

// Validates the JWT by minting a refresh against the backend. Returns
// the fresh access_token on success, or null on any failure (network,
// 401, schema mismatch) — caller falls back to the unauthenticated path.
async function validateViaRefresh(baseUrl, token, fetchImpl) {
  if (typeof fetchImpl !== 'function') return null

  const url = `${baseUrl.replace(/\/+$/, '')}/api/user/refresh`
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), REFRESH_TIMEOUT_MS)
  try {
    const response = await fetchImpl(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`
      },
      body: JSON.stringify({ client_version: 'desktop-bootstrap' }),
      signal: controller.signal
    })
    if (!response || !response.ok) return null

    const body = await response.json().catch(() => null)
    if (!body || typeof body.access_token !== 'string' || !body.access_token) return null

    const expiresInSec = Number.isFinite(body.expires_in) && body.expires_in > 0 ? body.expires_in : 8 * 60 * 60
    return {
      accessToken: body.access_token,
      tokenExpiresAt: Date.now() + expiresInSec * 1000,
      user:
        body.user && typeof body.user === 'object'
          ? { id: body.user.id ?? null, username: body.user.username ?? null }
          : null
    }
  } catch {
    return null
  } finally {
    clearTimeout(timeout)
  }
}

async function consumeBootstrapSession({
  deskagentHome,
  fetchImpl,
  log = () => {},
  env = process.env
} = {}) {
  if (!deskagentHome) return { status: 'no-home', snapshot: null }

  const filePath = env[BOOTSTRAP_ENV_VAR] || defaultBootstrapPath(deskagentHome)
  const parsed = readBootstrapFile(filePath)
  if (!parsed.ok) {
    if (parsed.code === 'missing') return { status: 'missing', snapshot: null }
    log(`[bootstrap-session] ignoring invalid bootstrap file (${parsed.code})`)
    deleteBootstrapFile(filePath)
    return { status: 'invalid', snapshot: null, code: parsed.code }
  }

  // Rename to `.consumed` BEFORE validating so a half-applied refresh can't
  // replay on the next launch.
  if (!renameToConsumed(filePath)) return { status: 'missing', snapshot: null }

  const refreshResult = await validateViaRefresh(parsed.session.baseUrl, parsed.session.token, fetchImpl)
  if (!refreshResult) {
    log('[bootstrap-session] backend refresh rejected; discarding')
    return { status: 'refresh-failed', snapshot: null }
  }

  log(`[bootstrap-session] consumed ${filePath}`)
  return {
    status: 'ok',
    snapshot: {
      baseUrl: parsed.session.baseUrl,
      token: refreshResult.accessToken,
      tokenExpiresAt: refreshResult.tokenExpiresAt,
      user: refreshResult.user || parsed.session.user
    }
  }
}

module.exports = {
  SCHEMA_VERSION,
  FILENAME,
  CONSUMED_SUFFIX,
  BOOTSTRAP_ENV_VAR,
  defaultBootstrapPath,
  readBootstrapFile,
  deleteBootstrapFile,
  renameToConsumed,
  validateViaRefresh,
  consumeBootstrapSession
}
