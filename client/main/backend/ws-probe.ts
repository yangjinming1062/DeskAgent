export const DEFAULT_CONNECT_TIMEOUT_MS = 10_000
export const DEFAULT_READY_GRACE_MS = 750

export interface ProbeOptions {
  WebSocketImpl?: any
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
    let socket: any

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
    } catch (error: any) {
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
      // Upgrade accepted. Give the server a brief window to reject the
      // credential post-handshake (early close) before declaring success.
      graceTimer = setTimeout(() => {
        finish({ ok: true })
      }, readyGraceMs)
    }

    const onMessage = () => {
      // Any frame means the gateway accepted us and is talking — unambiguous
      // success, no need to wait out the grace window.
      finish({ ok: true })
    }

    const onError = (event: any) => {
      finish({
        ok: false,
        reason: extractErrorReason(event) || 'WebSocket connection failed.'
      })
    }

    const onClose = (event: any) => {
      if (settled) {
        return
      }

      if (opened) {
        // Opened, then closed inside the grace window: the upgrade was accepted
        // but the session was refused.
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

function addListener(socket: any, type: string, handler: (...args: any[]) => void): void {
  if (typeof socket.addEventListener === 'function') {
    socket.addEventListener(type, handler)

    return
  }

  if (typeof socket.on === 'function') {
    socket.on(type, handler)
  }
}

function extractErrorReason(event: any): string {
  if (!event) {
    return ''
  }

  if (event instanceof Error) {
    return event.message
  }

  const err = event.error || event.message

  if (err instanceof Error) {
    return err.message
  }

  if (typeof err === 'string') {
    return err
  }

  return ''
}

function closeReason(event: any, fallback: string): string {
  const code = event && typeof event.code === 'number' ? event.code : null
  const reason = event && typeof event.reason === 'string' ? event.reason.trim() : ''

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
