import fs from 'node:fs'
import path from 'node:path'

import { atomicWriteFile } from '../utils'

export const FILENAME = 'desktop-settings.json'

let _storePath: null | string = null
let _config: Record<string, any> = {}
let _loaded = false
let _writeLock: null | Promise<any> = null
// 由 bridge 设置；登录前为 null。吞掉的错误表示 Runner 尚未连接。
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
    // ENOENT（首次运行）或 JSON 格式无效——从空配置开始。
  }

  return _config
}

/** 跨调用共享同一引用；变更必须经由 ``write`` / ``patch`` / ``mutate`` 进行。*/
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

  // 吞掉派发错误——bridge 在登录前可能尚未连接。
  if (_pushTarget && _config) {
    try {
      await _pushTarget(_config)
    } catch {
      /* runner 未连接——待下次 runner-ready 时再推送配置 */
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

/** fn 在写锁内就地变更配置；其返回值会作为 ``mutated`` 透出。*/
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
