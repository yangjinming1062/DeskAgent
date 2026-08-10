'use strict'

const fs = require('node:fs')
const path = require('node:path')

const { atomicWriteFile } = require('../utils.cjs')

const FILENAME = 'desktop-settings.json'

let _storePath = null
// undefined = not yet loaded; once loaded always a dict (possibly empty).
let _config = undefined
let _writeLock = null
// Set by the bridge; null pre-login. Swallowed errors = runner not connected yet.
let _pushTarget = null

function init({ deskagentHome }) {
  _storePath = deskagentHome ? path.join(deskagentHome, FILENAME) : null
}

function _load() {
  if (_config !== undefined) return _config
  _config = {}
  if (!_storePath) return _config
  try {
    const raw = fs.readFileSync(_storePath, 'utf8')
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      _config = parsed
    }
  } catch {
    // ENOENT (first run) or invalid JSON — start empty.
  }
  return _config
}

/** Same reference across calls; mutations must go through ``write`` / ``patch`` / ``mutate``. */
function read() {
  return _load()
}

function setPushTarget(fn) {
  _pushTarget = typeof fn === 'function' ? fn : null
}

async function _runLocked(task) {
  while (_writeLock) await _writeLock.catch(() => {})
  _writeLock = task()
  try {
    return await _writeLock
  } finally {
    _writeLock = null
  }
}

async function _persistAndPush() {
  if (_storePath) {
    const content = JSON.stringify(_config, null, 2)
    await atomicWriteFile(_storePath, content)
  }
  // Swallow dispatch errors — bridge may not be connected yet (pre-login).
  if (_pushTarget) {
    try {
      await _pushTarget(_config)
    } catch {
      /* runner not connected — config will be pushed on next runner-ready */
    }
  }
}

async function write(obj) {
  return _runLocked(async () => {
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) {
      return { ok: false, error: 'config must be a plain object' }
    }
    _config = obj
    await _persistAndPush()
    return { ok: true }
  })
}

async function patch(keyPath, { value, op = 'set' } = {}) {
  if (!Array.isArray(keyPath) || keyPath.length === 0) {
    return { ok: false, error: 'path must be a non-empty array' }
  }
  return _runLocked(async () => {
    _load()
    if (op === 'delete') {
      deleteIn(_config, keyPath)
    } else {
      setIn(_config, keyPath, value)
    }
    await _persistAndPush()
    return { ok: true }
  })
}

/** fn mutates the config in place under the write lock; its return is forwarded as ``mutated``. */
async function mutate(fn) {
  if (typeof fn !== 'function') {
    return { ok: false, error: 'mutate requires a function' }
  }
  let mutated
  await _runLocked(async () => {
    _load()
    mutated = fn(_config)
    await _persistAndPush()
  })
  return { ok: true, mutated }
}

function getDisabledSet(section = 'skills') {
  const raw = _load()?.[section]?.disabled
  if (!Array.isArray(raw)) return new Set()
  return new Set(raw.map(String))
}

function setIn(obj, keyPath, value) {
  let cursor = obj
  for (let i = 0; i < keyPath.length - 1; i++) {
    const k = keyPath[i]
    if (cursor[k] == null || typeof cursor[k] !== 'object') {
      cursor[k] = {}
    }
    cursor = cursor[k]
  }
  cursor[keyPath[keyPath.length - 1]] = value
}

function deleteIn(obj, keyPath) {
  let cursor = obj
  for (let i = 0; i < keyPath.length - 1; i++) {
    const k = keyPath[i]
    if (cursor[k] == null || typeof cursor[k] !== 'object') return
    cursor = cursor[k]
  }
  delete cursor[keyPath[keyPath.length - 1]]
}

module.exports = {
  FILENAME,
  init,
  read,
  write,
  patch,
  mutate,
  getDisabledSet,
  setPushTarget
}
