export const DEFAULT_CONNECT_TIMEOUT_MS = 10_000
export const DEFAULT_READY_GRACE_MS = 750

export interface WebSocketLike {
  addEventListener?: (type: string, listener: (...args: unknown[]) => void) => void
  close?: () => void
  on?: (type: string, listener: (...args: unknown[]) => void) => void
}

export type WebSocketConstructor = new (url: string) => WebSocketLike

export interface ProbeOptions {
  WebSocketImpl?: WebSocketConstructor
  connectTimeoutMs?: number
  readyGraceMs?: number
}

/**
 * Attempt a live WebSocket connection and classify the outcome.
 */
export function probeGatewayWebSocket(
  wsUrl: string,
  options: ProbeOptions = {}
): Promise<{ ok: boolean; reason?: string }> {
  const WebSocketImpl = options.WebSocketImpl
  const connectTimeoutMs = options.connectTimeoutMs ?? DEFAULT_CONNECT_TIMEOUT_MS
  const readyGraceMs = options.readyGraceMs ?? DEFAULT_READY_GRACE_MS

  if (typeof WebSocketImpl !== 'function') {
    return Promise.resolve({
      ok: false,
      reason: 'WebSocket is not available in this runtime.'
    })
  }

  return new Promise(resolve => {
    let settled = false
    let opened = false
    let connectTimer: NodeJS.Timeout | null = null
    let graceTimer: NodeJS.Timeout | null = null
    let socket: null | WebSocketLike = null

    const clearTimers = () => {
      if (connectTimer !== null) {
        clearTimeout(connectTimer)
        connectTimer = null
      }

      if (graceTimer !== null) {
        clearTimeout(graceTimer)
        graceTimer = null
      }
    }

    const finish = (result: { ok: boolean; reason?: string }) => {
      if (settled) {
        return
      }

      settled = true
      clearTimers()

      try {
        socket?.close?.()
      } catch {
        // ignore — best effort teardown
      }

      resolve(result)
    }

    try {
      socket = new WebSocketImpl(wsUrl)
    } catch (error: unknown) {
      finish({
        ok: false,
        reason: error instanceof Error ? error.message : String(error)
      })

      return
    }

    const onOpen = () => {
      if (settled) {
        return
      }

      opened = true
      graceTimer = setTimeout(() => {
        finish({ ok: true })
      }, readyGraceMs)
    }

    const onMessage = () => {
      finish({ ok: true })
    }

    const onError = (...args: unknown[]) => {
      finish({
        ok: false,
        reason: extractErrorReason(args[0]) || 'WebSocket connection failed.'
      })
    }

    const onClose = (...args: unknown[]) => {
      if (settled) {
        return
      }

      const event = args[0]

      if (opened) {
        finish({
          ok: false,
          reason: closeReason(event, 'The gateway accepted the connection then closed it (credential rejected?).')
        })

        return
      }

      finish({
        ok: false,
        reason: closeReason(event, 'The gateway closed the WebSocket before it opened.')
      })
    }

    addListener(socket, 'open', onOpen)
    addListener(socket, 'message', onMessage)
    addListener(socket, 'error', onError)
    addListener(socket, 'close', onClose)

    if (connectTimeoutMs > 0) {
      connectTimer = setTimeout(() => {
        finish({
          ok: false,
          reason: `Timed out after ${connectTimeoutMs}ms waiting for the WebSocket to open.`
        })
      }, connectTimeoutMs)
    }
  })
}

function addListener(socket: WebSocketLike, type: string, handler: (...args: unknown[]) => void): void {
  if (typeof socket.addEventListener === 'function') {
    socket.addEventListener(type, handler)

    return
  }

  if (typeof socket.on === 'function') {
    socket.on(type, handler)
  }
}

function extractErrorReason(event: unknown): string {
  if (!event) {
    return ''
  }

  if (event instanceof Error) {
    return event.message
  }

  const record = event as { error?: unknown; message?: unknown }
  const err = record.error || record.message

  if (err instanceof Error) {
    return err.message
  }

  if (typeof err === 'string') {
    return err
  }

  return ''
}

function closeReason(event: unknown, fallback: string): string {
  const record = event as { code?: unknown; reason?: unknown } | null | undefined
  const code = record && typeof record.code === 'number' ? record.code : null
  const reason = record && typeof record.reason === 'string' ? record.reason.trim() : ''

  if (code && reason) {
    return `${fallback} (code ${code}: ${reason})`
  }

  if (code) {
    return `${fallback} (code ${code})`
  }

  if (reason) {
    return `${fallback} (${reason})`
  }

  return fallback
}
