import { atom, type WritableAtom } from 'nanostores'

import { safeJsonParse } from './safe-json'

interface StorageKeyConfig {
  preserveOnLogout?: boolean
}

const REGISTERED_STORAGE_KEYS = new Map<string, StorageKeyConfig>()
const CLEAR_HANDLERS = new Set<() => void | Promise<void>>()

// clearCompanionStorage 单调递增的 epoch 计数器。OPFS 写操作在 commit 前对照 epoch：
// 不一致就视为过期登出，丢弃写入——避免上一位用户的 in-flight write 把新用户的
// 文件写到刚清空的 OPFS 目录里（典型场景：登出与新登入间隔 < OPFS clear I/O 时间）。
let clearEpoch = 0

/** 当前 clearCompanionStorage epoch；异步写操作在 commit 前用它判活。 */
export function currentClearEpoch(): number {
  return clearEpoch
}

export function registerCompanionStorageKey(key: string, config: StorageKeyConfig = {}): string {
  REGISTERED_STORAGE_KEYS.set(key, config)

  return key
}

export function registerStorageClearHandler(handler: () => void | Promise<void>): () => void {
  CLEAR_HANDLERS.add(handler)

  return () => {
    CLEAR_HANDLERS.delete(handler)
  }
}

export function storedBoolean(key: string, fallback: boolean): boolean {
  try {
    const value = window.localStorage.getItem(key)

    return value === null ? fallback : value === 'true'
  } catch {
    return fallback
  }
}

export function persistBoolean(key: string, value: boolean): void {
  try {
    window.localStorage.setItem(key, String(value))
  } catch {
    // 尽力而为：受限上下文可能抛出异常。
  }
}

export function storedString(key: string): null | string {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

export function persistString(key: string, value: null | string): void {
  try {
    if (value === null) {
      window.localStorage.removeItem(key)
    } else {
      window.localStorage.setItem(key, value)
    }
  } catch {
    // 尽力而为。
  }
}

export function storedJson<T>(key: string, fallback: T, validate?: (val: unknown) => val is T): T {
  const raw = storedString(key)

  if (!raw) {
    return fallback
  }

  const parsed = safeJsonParse<unknown>(raw, undefined)

  if (parsed === undefined) {
    return fallback
  }

  if (validate && !validate(parsed)) {
    return fallback
  }

  return parsed as T
}

export function storedEnum<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  const raw = storedString(key)

  if (raw !== null && (allowed as readonly string[]).includes(raw)) {
    return raw as T
  }

  return fallback
}

export function persistValidatedJson<T>(key: string, value: T, isPersistable: (val: T) => boolean): void {
  if (isPersistable(value)) {
    persistString(key, JSON.stringify(value))
  }
}

export interface PersistedAtomOptions<T> {
  key: string
  fallback: T
  isPersistable?: (val: unknown) => val is T
  preserveOnLogout?: boolean
}

export interface PersistedAtomResult<T> {
  $atom: WritableAtom<T>
  set: (next: T | Partial<T>) => void
  reset: () => void
  get: () => T
}

/** 统一定义持久化 Atom：自动加载本地缓存、瞬态不冲刷持久层、登出自动重置内存与持久化。 */
export function definePersistedAtom<T extends object>(options: PersistedAtomOptions<T>): PersistedAtomResult<T> {
  const { fallback, isPersistable, key, preserveOnLogout = false } = options
  registerCompanionStorageKey(key, { preserveOnLogout })

  const initial = storedJson<T>(key, fallback, isPersistable)
  const $atom = atom<T>(initial)

  function set(next: T | Partial<T>): void {
    const updated =
      typeof next === 'object' && next !== null && !Array.isArray(next) ? { ...$atom.get(), ...next } : (next as T)

    $atom.set(updated)

    if (!isPersistable || isPersistable(updated)) {
      persistString(key, JSON.stringify(updated))
    }
  }

  function reset(): void {
    $atom.set(fallback)
    persistString(key, null)
  }

  if (!preserveOnLogout) {
    registerStorageClearHandler(reset)
  }

  return {
    $atom,
    get: () => $atom.get(),
    reset,
    set
  }
}

export interface PersistedEnumOptions<T extends string> {
  key: string
  allowed: readonly T[]
  fallback: T
  preserveOnLogout?: boolean
}

export interface PersistedEnumResult<T extends string> {
  $atom: WritableAtom<T>
  set: (next: T) => void
  reset: () => void
  get: () => T
}

/** 统一定义持久化枚举：严格字面量类型校验、单一来源注册与登出生命周期绑定。 */
export function definePersistedEnum<T extends string>(options: PersistedEnumOptions<T>): PersistedEnumResult<T> {
  const { allowed, fallback, key, preserveOnLogout = false } = options
  registerCompanionStorageKey(key, { preserveOnLogout })

  const initial = storedEnum<T>(key, allowed, fallback)
  const $atom = atom<T>(initial)

  function set(next: T): void {
    $atom.set(next)
    persistString(key, next)
  }

  function reset(): void {
    $atom.set(fallback)
    persistString(key, null)
  }

  if (!preserveOnLogout) {
    registerStorageClearHandler(reset)
  }

  return {
    $atom,
    get: () => $atom.get(),
    reset,
    set
  }
}

export async function clearCompanionStorage(): Promise<void> {
  clearEpoch += 1

  try {
    for (const [key, config] of REGISTERED_STORAGE_KEYS.entries()) {
      if (!config.preserveOnLogout) {
        window.localStorage.removeItem(key)
      }
    }
  } catch {}

  // 触发所有上层模块注册的清理处理器（包含 OPFS 缓存清空与内存 Atom 状态重置）
  const tasks: Array<Promise<unknown> | void> = []

  for (const handler of CLEAR_HANDLERS) {
    try {
      const r = handler()

      if (r) {
        tasks.push(r)
      }
    } catch {}
  }

  // 真等所有 handler（包括异步 OPFS 清理）落地——调用方需在 $auth.set 前 await 此函数，
  // 避免 React 在 clear 完成前用陈旧 localStorage 值重渲染。
  await Promise.allSettled(tasks)
}
