const DEFAULT_TIMEOUT_MS = 15_000

class BackendRequestError extends Error {
  constructor({ status, code, message, body, cause }) {
    super(message)
    this.name = 'BackendRequestError'
    this.status = status ?? null
    this.code = code ?? null
    this.body = body ?? null
    if (cause) this.cause = cause
  }

  get isNetwork() {
    return this.status === null && this.code !== null
  }

  get isAuth() {
    return this.status === 401 || this.status === 403
  }

  get isClientError() {
    return Number.isInteger(this.status) && this.status >= 400 && this.status < 500
  }

  get isServerError() {
    return Number.isInteger(this.status) && this.status >= 500
  }
}

function normalizeBaseUrl(raw) {
  const value = String(raw || '').trim()
  if (!value) {
    throw new BackendRequestError({
      code: 'invalid-base-url',
      message: 'Backend base URL is required.'
    })
  }

  let parsed
  try {
    parsed = new URL(value)
  } catch (error) {
    throw new BackendRequestError({
      code: 'invalid-base-url',
      message: `Backend base URL is not valid: ${error.message}`
    })
  }

  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    throw new BackendRequestError({
      code: 'invalid-base-url',
      message: `Backend base URL must be http:// or https://, got ${parsed.protocol}`
    })
  }

  parsed.hash = ''
  parsed.search = ''
  parsed.pathname = parsed.pathname.replace(/\/+$/, '')
  return parsed.toString().replace(/\/+$/, '')
}

function resolveTimeoutMs(timeoutMs) {
  const parsed = Number(timeoutMs)
  if (Number.isFinite(parsed) && parsed > 0) return Math.round(parsed)
  return DEFAULT_TIMEOUT_MS
}

// JSON → JSON-stringified, string → as is, Buffer/Uint8Array → bytes.
function encodeBody(body) {
  if (body === undefined || body === null) return { body: undefined, contentType: null }
  if (typeof body === 'string') return { body, contentType: 'text/plain' }
  if (Buffer.isBuffer(body)) return { body, contentType: 'application/octet-stream' }
  if (body instanceof Uint8Array) return { body, contentType: 'application/octet-stream' }
  return { body: JSON.stringify(body), contentType: 'application/json' }
}

async function decodeResponseBody(res) {
  const contentType = res.headers.get('content-type') || ''
  const isJson = contentType.includes('application/json')
  const text = await res.text()

  if (!text) return isJson ? null : ''

  if (isJson) {
    try {
      return JSON.parse(text)
    } catch {
      return text
    }
  }

  return text
}

function createBackendClient(options = {}) {
  if (typeof options.fetch !== 'function') {
    throw new TypeError('createBackendClient requires a fetch implementation (pass options.fetch)')
  }

  const baseUrl = normalizeBaseUrl(options.baseUrl)
  const defaultTimeoutMs = resolveTimeoutMs(options.timeoutMs)
  const userAgent = options.userAgent || 'DeskAgentDesktop/0.15 (Electron)'

  async function request(method, path, { body, query, headers, token, timeoutMs, signal } = {}) {
    let url
    try {
      url = new URL(path, baseUrl.endsWith('/') ? baseUrl : `${baseUrl}/`)
    } catch (error) {
      throw new BackendRequestError({
        code: 'invalid-path',
        message: `Backend request path is not valid: ${error.message}`
      })
    }

    if (query && typeof query === 'object') {
      for (const [key, value] of Object.entries(query)) {
        if (value === undefined || value === null) continue
        url.searchParams.append(key, String(value))
      }
    }

    const { body: encodedBody, contentType } = encodeBody(body)
    const finalHeaders = {
      Accept: 'application/json',
      'User-Agent': userAgent,
      ...(contentType ? { 'Content-Type': contentType } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(headers || {})
    }

    const effectiveTimeoutMs = resolveTimeoutMs(timeoutMs ?? defaultTimeoutMs)
    const controller = new AbortController()
    const timeoutHandle = setTimeout(() => controller.abort(), effectiveTimeoutMs)

    if (signal) {
      if (signal.aborted) controller.abort()
      else signal.addEventListener('abort', () => controller.abort(), { once: true })
    }

    let res
    try {
      res = await options.fetch(url.toString(), {
        method,
        headers: finalHeaders,
        body: encodedBody,
        signal: controller.signal
      })
    } catch (error) {
      clearTimeout(timeoutHandle)
      const isAbort = error?.name === 'AbortError'
      throw new BackendRequestError({
        code: isAbort ? 'timeout' : 'network-error',
        message: isAbort
          ? `Backend request timed out after ${effectiveTimeoutMs}ms: ${method} ${url.pathname}`
          : `Backend request failed: ${method} ${url.pathname} (${error.message || error})`,
        cause: error
      })
    }

    clearTimeout(timeoutHandle)

    const payload = await decodeResponseBody(res)

    if (!res.ok) {
      const detail =
        payload && typeof payload === 'object' && typeof payload.detail === 'string'
          ? payload.detail
          : typeof payload === 'string' && payload
            ? payload
            : `${res.status} ${res.statusText || ''}`.trim()

      throw new BackendRequestError({
        status: res.status,
        code: `http-${res.status}`,
        message: `Backend ${method} ${url.pathname} failed: ${detail}`,
        body: payload
      })
    }

    return payload
  }

  return {
    baseUrl,
    request,
    get: (path, options) => request('GET', path, options),
    post: (path, options) => request('POST', path, options),
    put: (path, options) => request('PUT', path, options),
    patch: (path, options) => request('PATCH', path, options),
    delete: (path, options) => request('DELETE', path, options)
  }
}

module.exports = {
  BackendRequestError,
  createBackendClient,
  // Exported for tests that need to exercise base URL handling.
  normalizeBaseUrl,
  resolveTimeoutMs,
  DEFAULT_TIMEOUT_MS
}
