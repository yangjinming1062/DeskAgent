type GatewayEventName =
  | 'message.start'
  | 'message.delta'
  | 'message.complete'
  | 'tool.start'
  | 'tool.complete'
  | 'tool.call'
  | 'error'
  | (string & {})

export interface GatewayEvent<P = unknown> {
  payload?: P
  session_id?: string
  type: GatewayEventName
  seq?: number
}

export type ConnectionState = 'idle' | 'connecting' | 'open' | 'closed' | 'error'

type PendingCall = {
  reject: (error: Error) => void
  resolve: (value: unknown) => void
  timer?: ReturnType<typeof setTimeout>
}

export interface GatewayClientOptions {
  closedErrorMessage?: string
  connectErrorMessage?: string
  connectTimeoutMs?: number
  createRequestId?: (nextId: number) => number | string
  requestIdPrefix?: string
  requestTimeoutMs?: number
  socketFactory?: (url: string) => WebSocketLike
  notConnectedErrorMessage?: string
}

type GatewayRequestId = number | string
type JsonRpcFrame = {
  error?: { code?: number; data?: unknown; message?: string }
  id?: GatewayRequestId | null
  method?: string
  params?: GatewayEvent
  result?: unknown
  seq?: number
}
type WebSocketLike = WebSocket

// JSON-RPC 2.0 standard error codes. Mirror of the values emitted by
// `backend/core/jsonrpc_dispatcher.py` — keep this list in sync with the
// Python side so consumers can branch on `err.code` without parsing
// `err.message` heuristically.
export enum DeskAgentRpcErrorCode {
  ParseError = -32700,
  InvalidRequest = -32600,
  MethodNotFound = -32601,
  InvalidParams = -32602,
  InternalError = -32603
}

export class DeskAgentRpcError extends Error {
  readonly code: number
  readonly data?: unknown

  constructor(code: number, message: string, data?: unknown) {
    super(message)
    this.name = 'DeskAgentRpcError'
    this.code = code
    this.data = data
  }
}

const ANY = '*'
const DEFAULT_REQUEST_TIMEOUT_MS = 120_000
// A reconnect after sleep/wake must not hang forever in 'connecting' (which
// keeps the composer disabled and stuck on "Starting DeskAgent..."). If the open
// handshake doesn't land in this window, fail to 'error' so callers can retry.
const DEFAULT_CONNECT_TIMEOUT_MS = 15_000

export class JsonRpcGatewayClient {
  private nextId = 0
  private pending = new Map<GatewayRequestId, PendingCall>()
  private socket: WebSocketLike | null = null
  private state: ConnectionState = 'idle'
  private _lastCloseCode: number | null = null
  private _lastReceivedSeq = 0
  private _ackTimer: ReturnType<typeof setTimeout> | null = null
  private readonly eventHandlers = new Map<string, Set<(event: GatewayEvent) => void>>()
  private readonly stateHandlers = new Set<(state: ConnectionState) => void>()
  private readonly options: Required<Omit<GatewayClientOptions, 'socketFactory'>> &
    Pick<GatewayClientOptions, 'socketFactory'>

  constructor(options: GatewayClientOptions = {}) {
    this.options = {
      closedErrorMessage: options.closedErrorMessage ?? 'WebSocket closed',
      connectErrorMessage: options.connectErrorMessage ?? 'WebSocket connection failed',
      connectTimeoutMs: options.connectTimeoutMs ?? DEFAULT_CONNECT_TIMEOUT_MS,
      createRequestId: options.createRequestId ?? ((nextId: number) => `${options.requestIdPrefix ?? 'r'}${nextId}`),
      notConnectedErrorMessage: options.notConnectedErrorMessage ?? 'gateway not connected',
      requestIdPrefix: options.requestIdPrefix ?? 'r',
      requestTimeoutMs: options.requestTimeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS,
      socketFactory: options.socketFactory
    }
  }

  get connectionState(): ConnectionState {
    return this.state
  }

  /** Close code from the last WebSocket close event, or null if never closed. */
  get lastCloseCode(): number | null {
    return this._lastCloseCode
  }

  /** Monotonic sequence ID of the last received event frame from backend. */
  get lastReceivedSeq(): number {
    return this._lastReceivedSeq
  }

  resetSeq(seq = 0): void {
    this._lastReceivedSeq = seq
  }

  ackSeq(seq = this._lastReceivedSeq): void {
    if (seq > 0 && this.socket?.readyState === WebSocket.OPEN) {
      void this.request('session.ack', { seq }).catch(() => {})
    }
  }

  private scheduleAck(): void {
    if (this._ackTimer !== null) {
      return
    }

    this._ackTimer = setTimeout(() => {
      this._ackTimer = null
      this.ackSeq()
    }, 1000)
  }

  async connect(wsUrl: string): Promise<void> {
    if (this.socket?.readyState === WebSocket.OPEN || this.state === 'connecting') {
      return
    }

    this.setState('connecting')

    const socket = this.options.socketFactory?.(wsUrl) ?? new WebSocket(wsUrl)
    this.socket = socket

    socket.addEventListener('message', message => {
      if (this.socket !== socket) {
        return
      }

      this.handleMessage(message.data)
    })

    socket.addEventListener('close', (event: CloseEvent) => {
      if (this.socket !== socket) {
        return
      }

      this._lastCloseCode = event.code
      this.socket = null
      this.setState('closed')
      this.rejectAllPending(new Error(this.options.closedErrorMessage))
    })

    await new Promise<void>((resolve, reject) => {
      let settled = false
      let timer: ReturnType<typeof setTimeout> | undefined

      const cleanup = () => {
        if (timer !== undefined) {
          clearTimeout(timer)
        }

        socket.removeEventListener('open', onOpen)
        socket.removeEventListener('error', onError)
      }

      const onOpen = () => {
        if (settled || this.socket !== socket) {
          return
        }

        settled = true
        cleanup()
        this.setState('open')
        resolve()
      }

      const onError = () => {
        if (settled || this.socket !== socket) {
          return
        }

        settled = true
        cleanup()
        this.setState('error')
        reject(new Error(this.options.connectErrorMessage))
      }

      socket.addEventListener('open', onOpen, { once: true })
      socket.addEventListener('error', onError, { once: true })

      if (this.options.connectTimeoutMs > 0) {
        timer = setTimeout(() => {
          if (settled) {
            return
          }

          settled = true
          cleanup()

          // Drop the half-open socket so the next connect() starts clean
          // instead of short-circuiting on a zombie 'connecting' state.
          if (this.socket === socket) {
            try {
              socket.close()
            } catch {
              // ignore
            }

            this.socket = null
          }

          this.setState('error')
          reject(new Error(this.options.connectErrorMessage))
        }, this.options.connectTimeoutMs)
      }
    })
  }

  close(): void {
    if (this._ackTimer !== null) {
      clearTimeout(this._ackTimer)
      this._ackTimer = null
    }

    if (this.socket) {
      this.socket.close()
      this.socket = null
    }

    this.rejectAllPending(new Error(this.options.closedErrorMessage))
    this.setState('closed')
  }

  on<P = unknown>(type: GatewayEventName, handler: (event: GatewayEvent<P>) => void): () => void {
    let handlers = this.eventHandlers.get(type)

    if (!handlers) {
      handlers = new Set()
      this.eventHandlers.set(type, handlers)
    }

    handlers.add(handler as (event: GatewayEvent) => void)

    return () => handlers?.delete(handler as (event: GatewayEvent) => void)
  }

  onAny(handler: (event: GatewayEvent) => void): () => void {
    return this.on(ANY as GatewayEventName, handler)
  }

  onEvent(handler: (event: GatewayEvent) => void): () => void {
    return this.onAny(handler)
  }

  onState(handler: (state: ConnectionState) => void): () => void {
    this.stateHandlers.add(handler)
    handler(this.state)

    return () => this.stateHandlers.delete(handler)
  }

  request<T>(
    method: string,
    params: Record<string, unknown> = {},
    timeoutMs = this.options.requestTimeoutMs
  ): Promise<T> {
    const socket = this.socket

    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error(this.options.notConnectedErrorMessage))
    }

    const id = this.options.createRequestId(++this.nextId)

    return new Promise<T>((resolve, reject) => {
      const pending: PendingCall = {
        reject,
        resolve: value => resolve(value as T)
      }

      if (timeoutMs > 0) {
        pending.timer = setTimeout(() => {
          if (this.pending.delete(id)) {
            reject(new Error(`request timed out: ${method}`))
          }
        }, timeoutMs)
      }

      this.pending.set(id, pending)

      try {
        socket.send(
          JSON.stringify({
            jsonrpc: '2.0',
            id,
            method,
            params
          })
        )
      } catch (error) {
        this.clearPending(id)
        reject(error instanceof Error ? error : new Error(String(error)))
      }
    })
  }

  private handleMessage(raw: unknown): void {
    if (typeof raw !== 'string') {
      // Binary frames (Blob / ArrayBuffer) are not part of the JSON-RPC contract;
      // stringifying them would silently drop data, so reject explicitly.
      return
    }

    let frame: JsonRpcFrame

    try {
      frame = JSON.parse(raw) as JsonRpcFrame
    } catch {
      return
    }

    const seq = frame.params?.seq ?? frame.seq

    if (typeof seq === 'number') {
      if (seq <= this._lastReceivedSeq) {
        // Duplicate frame already processed — drop idempotently
        return
      }

      this._lastReceivedSeq = seq
      this.scheduleAck()
    }

    if (frame.id !== undefined && frame.id !== null) {
      const call = this.pending.get(frame.id)

      if (!call) {
        return
      }

      this.clearPending(frame.id)

      if (frame.error) {
        const code = typeof frame.error.code === 'number' ? frame.error.code : DeskAgentRpcErrorCode.InternalError
        call.reject(new DeskAgentRpcError(code, frame.error.message || 'DeskAgent RPC failed', frame.error.data))
      } else {
        call.resolve(frame.result)
      }

      return
    }

    if (frame.method === 'event' && frame.params?.type) {
      this.dispatchEvent(frame.params)
    }
  }

  private clearPending(id: GatewayRequestId): void {
    const call = this.pending.get(id)

    if (call?.timer) {
      clearTimeout(call.timer)
    }

    this.pending.delete(id)
  }

  private dispatchEvent(event: GatewayEvent): void {
    for (const handler of this.eventHandlers.get(event.type) ?? []) {
      handler(event)
    }

    for (const handler of this.eventHandlers.get(ANY) ?? []) {
      handler(event)
    }
  }

  private rejectAllPending(error: Error): void {
    for (const [id, call] of this.pending) {
      if (call.timer) {
        clearTimeout(call.timer)
      }

      call.reject(error)
      this.pending.delete(id)
    }
  }

  private setState(state: ConnectionState): void {
    if (this.state === state) {
      return
    }

    this.state = state

    for (const handler of this.stateHandlers) {
      handler(state)
    }
  }
}
