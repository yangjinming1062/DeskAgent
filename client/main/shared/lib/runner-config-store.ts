import fs from 'node:fs'
import path from 'node:path'

import { atomicWriteFile } from '../utils'

export const FILENAME = 'desktop-settings.json'

let _storePath: null | string = null
let _config: Record<string, any> = {}
let _loaded = false
let _writeLock: null | Promise<any> = null
// Set by the bridge; null pre-login. Swallowed errors = runner not connected yet.
let _pushTarget: null | ((config: Record<string, any>) => Promise<any> | void) = null

export function init({ spiritagentHome }: { spiritagentHome: null | string }): void {
  _storePath = spiritagentHome ? path.join(spiritagentHome, FILENAME) : null
  _loaded = false
}

function _load(): Record<string, any> {
  if (_loaded) {
    return _config
  }

  _loaded = true
  _config = {}

  if (!_storePath) {
    return _config
  }

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
export function read(): Record<string, any> {
  return _load()
}

export function setPushTarget(fn: null | ((config: Record<string, any>) => Promise<any> | void)): void {
  _pushTarget = typeof fn === 'function' ? fn : null
}

async function _runLocked<T>(task: () => Promise<T>): Promise<T> {
  while (_writeLock) {
    await _writeLock.catch(() => {})
  }

  _writeLock = task()

  try {
    return await _writeLock
  } finally {
    _writeLock = null
  }
}

async function _persistAndPush(): Promise<void> {
  if (_storePath) {
    const content = JSON.stringify(_config, null, 2)
    await atomicWriteFile(_storePath, content)
  }

  // Swallow dispatch errors — bridge may not be connected yet (pre-login).
  if (_pushTarget && _config) {
    try {
      await _pushTarget(_config)
    } catch {
      /* runner not connected — config will be pushed on next runner-ready */
    }
  }
}

export async function write(obj: unknown): Promise<{ error?: string; ok: boolean }> {
  return _runLocked(async () => {
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) {
      return { error: 'config must be a plain object', ok: false }
    }

    _config = obj as Record<string, any>
    await _persistAndPush()

    return { ok: true }
  })
}

export async function patch(
  keyPath: readonly (number | string)[],
  { op = 'set', value }: { op?: 'delete' | 'set'; value?: unknown } = {}
): Promise<{ error?: string; ok: boolean }> {
  if (!Array.isArray(keyPath) || keyPath.length === 0) {
    return { error: 'path must be a non-empty array', ok: false }
  }

  return _runLocked(async () => {
    _load()

    if (_config) {
      if (op === 'delete') {
        deleteIn(_config, keyPath)
      } else {
        setIn(_config, keyPath, value)
      }
    }

    await _persistAndPush()

    return { ok: true }
  })
}

/** fn mutates the config in place under the write lock; its return is forwarded as ``mutated``. */
export async function mutate<T>(
  fn: (config: Record<string, any>) => T
): Promise<{ error?: string; mutated?: T; ok: boolean }> {
  if (typeof fn !== 'function') {
    return { error: 'mutate requires a function', ok: false }
  }

  let mutated: T | undefined

  try {
    await _runLocked(async () => {
      _load()
      const snapshot = JSON.parse(JSON.stringify(_config ?? {}))

      try {
        if (_config) {
          mutated = fn(_config)
        }
      } catch (err) {
        _config = snapshot
        throw err
      }

      await _persistAndPush()
    })

    return { mutated, ok: true }
  } catch (err: any) {
    return { error: err.message, ok: false }
  }
}

export function getDisabledSet(section = 'skills'): Set<string> {
  const raw = _load()?.[section]?.disabled

  if (!Array.isArray(raw)) {
    return new Set()
  }

  return new Set(raw.map(String))
}

function setIn(obj: Record<string, any>, keyPath: readonly (number | string)[], value: unknown): void {
  let cursor: Record<string, any> = obj

  for (let i = 0; i < keyPath.length - 1; i++) {
    const k = keyPath[i]

    if (cursor[k] == null || typeof cursor[k] !== 'object') {
      cursor[k] = {}
    }

    cursor = cursor[k]
  }

  cursor[keyPath[keyPath.length - 1]] = value
}

function deleteIn(obj: Record<string, any>, keyPath: readonly (number | string)[]): void {
  let cursor: Record<string, any> = obj

  for (let i = 0; i < keyPath.length - 1; i++) {
    const k = keyPath[i]

    if (cursor[k] == null || typeof cursor[k] !== 'object') {
      return
    }

    cursor = cursor[k]
  }

  delete cursor[keyPath[keyPath.length - 1]]
}
