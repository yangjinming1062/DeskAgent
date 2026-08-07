'use strict'

const fs = require('node:fs')
const path = require('node:path')
const { app } = require('electron')

// Resolution priority (first hit wins):
//   1. $DESKAGENT_HOME/desktop-config.json — last user-confirmed backend URL
//   2. process.resourcesPath/config.json — packaged default
//   3. <repo>/client/config.json — dev default
//   4. DEFAULT_BACKEND_URL — last-resort fallback

const DEFAULT_BACKEND_URL = 'http://localhost:8000'

let cached = null

function getConfigPath() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'config.json')
  }
  return path.join(__dirname, '..', 'config.json')
}

function loadConfig() {
  if (cached) return cached

  const p = getConfigPath()
  try {
    cached = JSON.parse(fs.readFileSync(p, 'utf-8'))
  } catch (err) {
    if (err.code !== 'ENOENT') {
      console.error('[Config] Failed to load', p, err)
    }
    cached = { backendUrl: DEFAULT_BACKEND_URL }
  }
  return cached
}

function getBackendUrl() {
  return loadConfig().backendUrl
}

// Coerce + trailing-slash strip; most callers append a path suffix and assume
// a slash-free base (see `resolveRemoteBackend`, `setupAutoUpdater`).
function getNormalizedBackendUrl() {
  return String(getBackendUrl() || '').replace(/\/+$/, '')
}

// Same as above but honors the persisted override via `deskagentHome` —
// callers that surface user-facing URLs (login prefill, auto-updater feed,
// remote backend resolution) must go through this so a non-default
// backend survives logout + relaunch.
function resolveNormalizedBackendUrl(deskagentHome) {
  return String(resolveBackendUrl(deskagentHome) || '').replace(/\/+$/, '')
}

// $DESKAGENT_HOME/desktop-config.json holds the user's last-entered backend
// URL (kept distinct from the encrypted session file at `agent-session.json`
// so it survives a logout). Best-effort: missing / malformed file yields
// null and the caller falls through to the bundled config.
const FILENAME = 'desktop-config.json'

function configPath(deskagentHome) {
  if (!deskagentHome) return null
  return path.join(deskagentHome, FILENAME)
}

function readStoredBackendUrl(deskagentHome) {
  const target = configPath(deskagentHome)
  if (!target) return null
  try {
    const parsed = JSON.parse(fs.readFileSync(target, 'utf8'))
    if (parsed && typeof parsed.backendUrl === 'string' && parsed.backendUrl.trim()) {
      return parsed.backendUrl.trim()
    }
  } catch {
    // missing / malformed / unreadable → fall through to bundled
  }
  return null
}

function writeStoredBackendUrl(deskagentHome, backendUrl) {
  const target = configPath(deskagentHome)
  if (!target || typeof backendUrl !== 'string' || !backendUrl.trim()) return false

  let existing = {}
  try {
    existing = JSON.parse(fs.readFileSync(target, 'utf8'))
    if (!existing || typeof existing !== 'object') existing = {}
  } catch {
    existing = {}
  }

  existing.backendUrl = backendUrl.trim()
  existing.savedAt = Date.now()

  try {
    fs.mkdirSync(path.dirname(target), { recursive: true })
    const tmp = `${target}.${process.pid}.${Date.now()}.tmp`
    fs.writeFileSync(tmp, JSON.stringify(existing, null, 2), 'utf8')
    fs.renameSync(tmp, target)
    if (process.platform !== 'win32') {
      try {
        fs.chmodSync(target, 0o600)
      } catch {
        // best-effort; FS may not support chmod
      }
    }
    return true
  } catch {
    return false
  }
}

// Single source of truth for "what backend URL should this process use?".
// Pass `deskagentHome` (typically `DESKAGENT_HOME` from entry.cjs) to honor
// the persisted user override; omit to get the bundled-only chain.
function resolveBackendUrl(deskagentHome) {
  const stored = readStoredBackendUrl(deskagentHome)
  if (stored) return stored
  return getBackendUrl() || DEFAULT_BACKEND_URL
}

module.exports = {
  getBackendUrl,
  getNormalizedBackendUrl,
  loadConfig,
  resolveBackendUrl,
  resolveNormalizedBackendUrl,
  readStoredBackendUrl,
  writeStoredBackendUrl,
  configPath,
  FILENAME,
  DEFAULT_BACKEND_URL
}
