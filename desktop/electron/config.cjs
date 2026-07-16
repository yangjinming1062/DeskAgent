'use strict'

// Resolution priority (first hit wins):
//   1. process.resourcesPath/config.json — packaged default
//   2. <repo>/desktop/config.json — dev default
//   3. DEFAULT_BACKEND_URL — last-resort fallback
const fs = require('node:fs')
const path = require('node:path')
const { app } = require('electron')

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

module.exports = { getBackendUrl, getNormalizedBackendUrl, loadConfig, DEFAULT_BACKEND_URL }
