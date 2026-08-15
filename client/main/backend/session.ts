import fs from 'node:fs'
import path from 'node:path'

import type { SafeStorageApi } from '../security/hardening'

import { type BackendClient, BackendRequestError, createBackendClient } from './client'

export const SESSION_FILENAME = 'agent-session.json'
export const SESSION_SCHEMA_VERSION = 2
export const KNOWN_TOKEN_TTL_MS = 8 * 60 * 60 * 1000
export const REFRESH_LEAD_MS = 5 * 60 * 1000

export interface SessionErrorOptions {
  cause?: unknown
  code: string
  message: string
  status?: number
}

export class SessionError extends Error {
  code: string
  status?: number

  constructor({ cause, code, message, status }: SessionErrorOptions) {
    super(message)
    this.name = 'SessionError'
    this.code = code

    if (cause) {
      this.cause = cause
    }

    if (status !== undefined) {
      this.status = status
    }
  }
}

function atomicWriteJson(targetPath: string, payload: unknown): void {
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

function readJsonSafe(filePath: string): any {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'))
  } catch {
    return null
  }
}

export function encryptToken(
  raw: null | string | undefined,
  safeStorage?: null | SafeStorageApi
): null | { encoding: 'safeStorage'; value: string } {
  const value = String(raw || '')

  if (!value) {
    return null
  }

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

export function decryptToken(blob: any, safeStorage?: null | SafeStorageApi): null | string {
  if (!blob || typeof blob !== 'object') {
    return null
  }

  if (blob.encoding !== 'safeStorage') {
    return null
  }

  if (!safeStorage?.isEncryptionAvailable?.()) {
    return null
  }

  try {
    const buf = Buffer.from(String(blob.value || ''), 'base64')

    return safeStorage.decryptString ? safeStorage.decryptString(buf) : null
  } catch {
    return null
  }
}

function normalizeUser(raw: any): null | { id: null | number; username: null | string } {
  if (!raw || typeof raw !== 'object') {
    return null
  }

  const id = raw.id ?? raw.user_id ?? null
  const username = raw.username ?? null

  if (id === null && username === null) {
    return null
  }

  return {
    id: id === null ? null : Number(id),
    username: username === null ? null : String(username)
  }
}

export function decodeActivationCode(code: string): { baseUrl: string; token: string } {
  const padding = '='.repeat((4 - (code.length % 4)) % 4)
  const raw = Buffer.from(code + padding, 'base64url').toString('utf8')
  const data = JSON.parse(raw)
  const baseUrl = data.b
  const token = data.t

  if (!baseUrl || !token) {
    throw new Error('activation code missing required fields')
  }

  return { baseUrl, token }
}

export interface BackendSessionOptions {
  appVersion?: string
  defaultBaseUrl?: null | string
  fetchImpl: (url: string, init?: any) => Promise<any>
  log?: (chunk: string) => void
  now?: () => number
  safeStorage?: null | SafeStorageApi
  userDataDir: string
}

export interface SessionSnapshot {
  baseUrl: null | string
  hasToken: boolean
  tokenExpiresAt: null | number
  user: null | { id: null | number; username: null | string }
}

export interface BackendSession {
  _sessionPath: string
  activate: (payload?: { clientContext?: any; code?: string }) => Promise<null | SessionSnapshot>
  authHeaders: () => Record<string, string>
  clearSession: () => void
  getSession: () => null | SessionSnapshot
  getToken: () => null | string
  logout: () => Promise<{ backendUnreachable?: boolean; error?: string; ok: boolean }>
  refresh: (payload?: { clientContext?: any }) => Promise<null | SessionSnapshot>
  restoreSession: () => Promise<null | SessionSnapshot>
}

export function createBackendSession(options: BackendSessionOptions): BackendSession {
  const {
    appVersion = 'unknown',
    defaultBaseUrl = null,
    fetchImpl,
    now = () => Date.now(),
    safeStorage = null,
    userDataDir
  } = options

  if (!userDataDir) {
    throw new SessionError({ code: 'missing-user-data-dir', message: 'userDataDir is required' })
  }

  if (typeof fetchImpl !== 'function') {
    throw new SessionError({ code: 'missing-fetch', message: 'fetch implementation is required' })
  }

  const sessionPath = path.join(userDataDir, SESSION_FILENAME)

  const log = typeof options.log === 'function' ? options.log : () => {}

  let cached: null | {
    activationCode: string
    baseUrl: null | string
    token: null | string
    tokenExpiresAt: null | number
    user: null | { id: null | number; username: null | string }
  } = null

  let backendClient: null | BackendClient = null
  let backendClientBaseUrl: null | string = null
  let activatePromise: null | Promise<null | SessionSnapshot> = null
  let refreshTimer: NodeJS.Timeout | null = null

  function persistCurrent(): void {
    if (!cached) {
      return
    }

    atomicWriteJson(sessionPath, {
      activationCode: encryptToken(cached.activationCode, safeStorage),
      baseUrl: cached.baseUrl,
      savedAt: now(),
      schemaVersion: SESSION_SCHEMA_VERSION,
      user: cached.user
    })
  }

  function clearRefreshTimer(): void {
    if (refreshTimer !== null) {
      clearTimeout(refreshTimer)
      refreshTimer = null
    }
  }

  function scheduleRefresh(): void {
    clearRefreshTimer()

    if (!cached?.tokenExpiresAt) {
      return
    }

    const delay = cached.tokenExpiresAt - now() - REFRESH_LEAD_MS

    if (delay <= 0) {
      return
    }

    refreshTimer = setTimeout(() => {
      refreshTimer = null

      if (!cached?.token) {
        return
      }

      log('[session] proactive token refresh triggered')
      refresh().catch(err => {
        log(`[session] proactive refresh failed: ${err?.message || err}`)
      })
    }, delay)

    if (typeof refreshTimer.unref === 'function') {
      refreshTimer.unref()
    }
  }

  function loadFromDisk(): null | {
    activationCode: string
    baseUrl: null | string
    savedAt: null | number
    user: null | { id: null | number; username: null | string }
  } {
    const raw = readJsonSafe(sessionPath)

    if (!raw || typeof raw !== 'object') {
      return null
    }

    if (raw.schemaVersion !== SESSION_SCHEMA_VERSION) {
      return null
    }

    const activationCode = decryptToken(raw.activationCode, safeStorage)

    if (!activationCode) {
      return null
    }

    return {
      activationCode,
      baseUrl: typeof raw.baseUrl === 'string' ? raw.baseUrl : null,
      savedAt: Number.isFinite(raw.savedAt) ? raw.savedAt : null,
      user: normalizeUser(raw.user)
    }
  }

  function snapshot(): null | SessionSnapshot {
    if (!cached) {
      return null
    }

    const { baseUrl, token, tokenExpiresAt, user } = cached

    return { baseUrl, hasToken: Boolean(token), tokenExpiresAt, user }
  }

  function effectiveBaseUrl(): null | string {
    if (cached?.baseUrl) {
      return cached.baseUrl
    }

    if (defaultBaseUrl) {
      return defaultBaseUrl
    }

    return null
  }

  function client(): BackendClient {
    const baseUrl = effectiveBaseUrl()

    if (!baseUrl) {
      throw new SessionError({
        code: 'no-base-url',
        message: 'Backend base URL is not configured. Activate with a valid activation code.'
      })
    }

    if (backendClient && backendClientBaseUrl === baseUrl) {
      return backendClient
    }

    backendClient = createBackendClient({ baseUrl, fetch: fetchImpl })
    backendClientBaseUrl = baseUrl

    return backendClient
  }

  function translateBackendError(error: any): never {
    if (!(error instanceof BackendRequestError)) {
      throw error
    }

    if (error.status === 401) {
      throw new SessionError({
        cause: error,
        code: 'bad-credentials',
        message: '激活码无效。',
        status: 401
      })
    }

    throw new SessionError({
      cause: error,
      code: error.code || 'backend-error',
      message: error.message,
      status: error.status ?? undefined
    })
  }

  function applySession({
    activationCode,
    baseUrl,
    source,
    token,
    tokenExpiresAt,
    user
  }: {
    activationCode?: null | string
    baseUrl?: null | string
    source: string
    token: string
    tokenExpiresAt: null | number
    user?: any
  }): null | SessionSnapshot {
    if (!token) {
      throw new SessionError({
        code: 'no-token',
        message: 'Cannot apply a session without a session token.'
      })
    }

    const resolvedBaseUrl = baseUrl || cached?.baseUrl || null
    const resolvedUser = normalizeUser(user) || cached?.user || { id: null, username: null }
    const resolvedCode = activationCode || cached?.activationCode || null

    if (!resolvedCode) {
      throw new SessionError({
        code: 'no-activation-code',
        message: 'Cannot apply a session without an activation code.'
      })
    }

    cached = {
      activationCode: resolvedCode,
      baseUrl: resolvedBaseUrl,
      token,
      tokenExpiresAt,
      user: resolvedUser
    }

    backendClient = null
    backendClientBaseUrl = null

    persistCurrent()
    scheduleRefresh()

    log(`[session] ${source} ok base=${resolvedBaseUrl} user=${resolvedUser?.username ?? '?'}`)

    return snapshot()
  }

  function activate(payload: { clientContext?: any; code?: string } = {}): Promise<null | SessionSnapshot> {
    const { clientContext, code } = payload

    if (!code) {
      throw new SessionError({
        code: 'missing-code',
        message: 'Activation code is required.'
      })
    }

    let baseUrl: string

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

    if (activatePromise) {
      return activatePromise
    }

    const backend = createBackendClient({ baseUrl, fetch: fetchImpl })
    activatePromise = backend
      .post<any>('/api/user/activate', {
        body: {
          client_context: clientContext || undefined,
          client_version: appVersion,
          code
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
          activationCode: code,
          baseUrl,
          source: 'activate',
          token: response.access_token,
          tokenExpiresAt: now() + expiresIn,
          user: response.user
        })
      })
      .catch(translateBackendError)
      .finally(() => {
        activatePromise = null
      })

    return activatePromise
  }

  function refresh(payload: { clientContext?: any } = {}): Promise<null | SessionSnapshot> {
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
      .post<any>('/api/user/refresh', {
        body: {
          client_context: clientContext || undefined,
          client_version: appVersion
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
          source: 'refresh',
          token: response.access_token,
          tokenExpiresAt: now() + expiresIn,
          user: response.user
        })
      })
      .catch(translateBackendError)
  }

  function logout(): Promise<{ backendUnreachable?: boolean; error?: string; ok: boolean }> {
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
        log(`[session] logout backend call failed: ${error?.message || error}`)
        clearSession()

        return { backendUnreachable: true, error: error?.message || String(error), ok: true }
      })
  }

  function clearSession(): void {
    clearRefreshTimer()
    cached = null
    backendClient = null
    backendClientBaseUrl = null

    try {
      fs.unlinkSync(sessionPath)
    } catch (error: any) {
      if (error?.code !== 'ENOENT') {
        log(`[session] clearSession unlink failed: ${error.message}`)
      }
    }
  }

  async function restoreSession(): Promise<null | SessionSnapshot> {
    const loaded = loadFromDisk()

    if (!loaded) {
      return null
    }

    cached = {
      activationCode: loaded.activationCode,
      baseUrl: loaded.baseUrl,
      token: null,
      tokenExpiresAt: null,
      user: loaded.user
    }

    try {
      return await activate({ code: loaded.activationCode })
    } catch (err: any) {
      if (err instanceof SessionError && err.code === 'bad-credentials') {
        clearSession()
      }

      log(`[session] restore activation failed: ${err?.message || err}`)

      return null
    }
  }

  function getSession(): null | SessionSnapshot {
    return snapshot()
  }

  function getToken(): null | string {
    return cached?.token ?? null
  }

  function authHeaders(): Record<string, string> {
    if (!cached?.token) {
      return {}
    }

    return { Authorization: `Bearer ${cached.token}` }
  }

  return {
    _sessionPath: sessionPath,
    activate,
    authHeaders,
    clearSession,
    getSession,
    getToken,
    logout,
    refresh,
    restoreSession
  }
}
