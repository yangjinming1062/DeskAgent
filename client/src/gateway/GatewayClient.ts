import type { GatewayState, RpcEvent, RpcMessage, RpcResponse, RpcNotification } from './types'

const RPC_TIMEOUT_MS = 30_000

export class GatewayClient {
  private ws: WebSocket | null = null
  private callId = 0
  private pending = new Map<number, { resolve: (v: unknown) => void; reject: (e: Error) => void; timer: ReturnType<typeof setTimeout> }>()
  private eventHandlers = new Set<(e: RpcEvent) => void>()
  private stateHandlers = new Set<(s: GatewayState) => void>()
  private currentState: GatewayState = 'idle'
  private connectResolve: (() => void) | null = null
  private connectReject: ((err: Error) => void) | null = null

  get state(): GatewayState {
    return this.currentState
  }

  connect(wsUrl: string): Promise<void> {
    this.close()
    this.setState('connecting')
    this.ws = new WebSocket(wsUrl)

    return new Promise((resolve, reject) => {
      this.connectResolve = resolve
      this.connectReject = reject
      if (!this.ws) {
        this.settleConnect(new Error('WebSocket not created'))
        return
      }
      this.ws.onopen = () => { this.setState('open'); this.settleConnect() }
      this.ws.onerror = () => {
        this.setState('error')
        if (this.currentState !== 'open') this.settleConnect(new Error('WebSocket connection failed'))
      }
      this.ws.onclose = () => {
        this.setState('closed')
        this.failAllPending(new Error('WebSocket closed'))
      }
      this.ws.onmessage = (e) => {
        try {
          this.handleMessage(JSON.parse(e.data) as RpcMessage)
        } catch {
          // Malformed frame — ignore
        }
      }
    })
  }

  request<T>(method: string, params?: Record<string, unknown>): Promise<T> {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error('WebSocket not connected'))
    }
    const id = ++this.callId
    const timer = setTimeout(() => {
      const p = this.pending.get(id)
      if (p) { this.pending.delete(id); p.reject(new Error(`RPC timeout: ${method}`)) }
    }, RPC_TIMEOUT_MS)

    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve: resolve as (v: unknown) => void, reject, timer })
      this.ws!.send(JSON.stringify({ jsonrpc: '2.0', id, method, params: params ?? {} }))
    })
  }

  onEvent(handler: (e: RpcEvent) => void): () => void {
    this.eventHandlers.add(handler)
    return () => this.eventHandlers.delete(handler)
  }

  onState(handler: (s: GatewayState) => void): () => void {
    this.stateHandlers.add(handler)
    handler(this.currentState)
    return () => this.stateHandlers.delete(handler)
  }

  close(): void {
    this.settleConnect(new Error('Connection closed by client'))
    this.failAllPending(new Error('Connection closed by client'))
    if (this.ws) {
      this.ws.onopen = null
      this.ws.onerror = null
      this.ws.onclose = null
      this.ws.onmessage = null
      if (this.ws.readyState === WebSocket.OPEN) this.ws.close(1000)
      this.ws = null
    }
  }

  private settleConnect(err?: Error): void {
    if (this.connectResolve && !err) {
      this.connectResolve()
    } else if (this.connectReject && err) {
      this.connectReject(err)
    }
    this.connectResolve = null
    this.connectReject = null
  }

  private handleMessage(msg: RpcMessage): void {
    if (isResponse(msg)) {
      const p = this.pending.get(msg.id)
      if (p) {
        clearTimeout(p.timer)
        this.pending.delete(msg.id)
        if (msg.error) p.reject(new Error(msg.error.message))
        else p.resolve(msg.result)
      }
      return
    }
    if (isEventNotification(msg)) {
      this.eventHandlers.forEach(h => h(msg.params))
    }
    // Other notification methods (e.g. system.keepalive) are intentionally dropped
  }

  private setState(s: GatewayState): void {
    this.currentState = s
    this.stateHandlers.forEach(h => h(s))
  }

  private failAllPending(err: Error): void {
    for (const [, p] of this.pending) {
      clearTimeout(p.timer)
      p.reject(err)
    }
    this.pending.clear()
  }
}

function isResponse(m: RpcMessage): m is RpcResponse {
  return 'id' in m && ('result' in m || 'error' in m)
}

function isEventNotification(m: RpcMessage): m is RpcNotification {
  return !('id' in m) && 'method' in m && m.method === 'event'
}
