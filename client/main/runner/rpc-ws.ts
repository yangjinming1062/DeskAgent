import { EventEmitter } from 'node:events'
import fs from 'node:fs'
import http from 'node:http'

import type WebSocket from 'ws'
import { WebSocketServer } from 'ws'

export const DEFAULT_TIMEOUT_MS = 120_000
export const JSON_RPC_VERSION = '2.0'
export const HEARTBEAT_INTERVAL_MS = 10_000
export const HEARTBEAT_DEADLINE_MS = 120_000

export interface CreateRunnerWsServerOptions {
  authToken?: string
  log?: (chunk: string) => void
  onReverseRpc?: null | ((method: string, params: any) => Promise<any>)
}

export interface RunnerWsStatus {
  connected: boolean
  path: null | string
  pendingCalls: number
  transport: null | string
}

export interface RunnerWsServer {
  call: <T = unknown>(
    method: string,
    params?: Record<string, unknown>,
    options?: { id?: number | string; timeoutMs?: number }
  ) => Promise<T>
  getStatus: () => RunnerWsStatus
  onEvent: (callback: (event: any) => void) => () => void
  start: (options?: { path?: string }) => Promise<{ path: null | string; transport: null | string }>
  stop: () => Promise<{ ok: boolean }>
}

export function createRunnerWsServer(options: CreateRunnerWsServerOptions = {}): RunnerWsServer {
  const log = typeof options.log === 'function' ? options.log : () => {}
  const onReverseRpc = typeof options.onReverseRpc === 'function' ? options.onReverseRpc : null
  const authToken = typeof options.authToken === 'string' ? options.authToken : ''
  const emitter = new EventEmitter()

  const pending = new Map<
    string,
    { method: string; reject: (err: Error) => void; resolve: (val: any) => void; timer: NodeJS.Timeout }
  >()

  const httpServer = http.createServer()
  let wss: null | WebSocketServer = null
  let activeWs: null | WebSocket = null
  let nextId = 1
  let transport: null | string = null
  let ipcPath: null | string = null
  let upgradeHandler: null | ((req: http.IncomingMessage, socket: any, head: Buffer) => void) = null
  let closed = false

  function emit(event: any): void {
    emitter.emit('event', event)
  }

  function rejectAllPending(error: Error): void {
    for (const [id, entry] of pending.entries()) {
      clearTimeout(entry.timer)
      entry.reject(error)
      pending.delete(id)
    }
  }

  function sendToRunner(payload: any): boolean {
    if (!activeWs || activeWs.readyState !== 1) {
      return false
    }

    try {
      activeWs.send(JSON.stringify(payload))

      return true
    } catch (error: any) {
      log(`[runner-ws] send failed: ${error.message}`)

      return false
    }
  }

  function handleRunnerMessage(raw: any): void {
    let message: any

    try {
      message = JSON.parse(typeof raw === 'string' ? raw : raw.toString())
    } catch (error: any) {
      log(`[runner-ws] parse error: ${error.message}`)

      return
    }

    if (!message || typeof message !== 'object') {
      return
    }

    const id = message.id
    const method = message.method

    // Response to a pending outbound call (has id, no method)
    if (id !== undefined && id !== null && !method) {
      const entry = pending.get(String(id))

      if (entry) {
        pending.delete(String(id))
        clearTimeout(entry.timer)

        if (Object.prototype.hasOwnProperty.call(message, 'error')) {
          const err = message.error || {}
          entry.reject(new Error(typeof err.message === 'string' ? err.message : `Runner error for ${entry.method}`))
        } else {
          entry.resolve(message.result)
        }
      } else {
        log(`[runner-ws] response for unknown id=${id}`)
      }

      return
    }

    // Inbound request from Runner (has id + method) — reverse RPC
    if (id !== undefined && id !== null && method) {
      handleReverseRpc(id, method, message.params || {})

      return
    }

    // Notification (no id, has method)
    if (method) {
      if (method === 'runner_ready') {
        log('[runner-ws] runner_ready received')
        emit({
          capabilities: message.params?.capabilities ?? null,
          probe_failed: message.params?.probe_failed ?? null,
          type: 'runner_ready',
          version: message.params?.version ?? null
        })
      } else if (method === 'tools_changed') {
        emit({ type: 'tools_changed' })
      } else {
        emit({ method, params: message.params || {}, type: 'notification' })
      }

      return
    }
  }

  async function handleReverseRpc(id: any, method: string, params: any): Promise<void> {
    if (!onReverseRpc) {
      sendToRunner({
        error: { code: -32601, message: `No handler for reverse RPC: ${method}` },
        id,
        jsonrpc: JSON_RPC_VERSION
      })

      return
    }

    try {
      const result = await onReverseRpc(method, params)
      sendToRunner({ id, jsonrpc: JSON_RPC_VERSION, result })
    } catch (error: any) {
      sendToRunner({
        error: { code: -32000, message: error?.message || String(error) },
        id,
        jsonrpc: JSON_RPC_VERSION
      })
    }
  }

  function call<T = unknown>(
    method: string,
    params: Record<string, unknown> = {},
    { id: explicitId, timeoutMs }: { id?: number | string; timeoutMs?: number } = {}
  ): Promise<T> {
    if (closed) {
      return Promise.reject(new Error('Runner WS server is closed.'))
    }

    const id = explicitId !== undefined ? String(explicitId) : `call_${nextId++}`

    const effectiveTimeoutMs =
      typeof timeoutMs === 'number' && Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : DEFAULT_TIMEOUT_MS

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (!pending.has(id)) {
          return
        }

        pending.delete(id)
        reject(new Error(`Runner call '${method}' (id=${id}) timed out after ${effectiveTimeoutMs}ms.`))
      }, effectiveTimeoutMs)

      pending.set(id, { method, reject, resolve, timer })

      const sent = sendToRunner({
        id,
        jsonrpc: JSON_RPC_VERSION,
        method,
        params
      })

      if (!sent) {
        pending.delete(id)
        clearTimeout(timer)
        reject(new Error('Runner is not connected.'))
      }
    })
  }

  function listenOnce(targetPath: string): Promise<void> {
    return new Promise((resolve, reject) => {
      if (process.platform !== 'win32') {
        try {
          fs.unlinkSync(targetPath)
        } catch (error: any) {
          if (error?.code !== 'ENOENT') {
            reject(error)

            return
          }
        }
      }

      const onError = (error: Error) => {
        httpServer.removeListener('error', onError)
        reject(error)
      }

      httpServer.once('error', onError)
      const previousUmask = process.platform === 'win32' ? null : process.umask(0o077)

      try {
        httpServer.listen(targetPath, () => {
          httpServer.removeListener('error', onError)
          resolve()
        })
      } finally {
        if (previousUmask !== null) {
          process.umask(previousUmask)
        }
      }
    })
  }

  async function start({ path: requestedPath }: { path?: string } = {}): Promise<{
    path: null | string
    transport: null | string
  }> {
    if (wss) {
      return Promise.resolve({ path: ipcPath, transport })
    }

    if (!requestedPath || typeof requestedPath !== 'string') {
      throw new Error('start() requires the Desktop IPC path (named pipe or socket path).')
    }

    if (!authToken) {
      throw new Error('createRunnerWsServer requires an authToken — the IPC link has no other access gate.')
    }

    transport = process.platform === 'win32' ? 'pipe' : 'unix'
    ipcPath = requestedPath

    try {
      await listenOnce(ipcPath)
    } catch (error: any) {
      if (error?.code !== 'EADDRINUSE') {
        throw error
      }

      log(`[runner-ws] ${ipcPath} busy; retrying once in 200ms`)
      await new Promise(resolve => setTimeout(resolve, 200))
      await listenOnce(ipcPath)
    }

    log(`[runner-ws] listening on ${ipcPath}`)

    try {
      wss = new WebSocketServer({ noServer: true })

      upgradeHandler = (req, socket, head) => {
        if (req.headers['x-deskagent-auth'] !== authToken) {
          log('[runner-ws] rejected upgrade: bad handshake token')
          socket.end('HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n')

          return
        }

        wss!.handleUpgrade(req, socket, head, (ws: WebSocket) => wss!.emit('connection', ws, req))
      }

      httpServer.on('upgrade', upgradeHandler)

      wss.on('connection', (ws: WebSocket, req: http.IncomingMessage) => {
        log(`[runner-ws] runner connected over ${transport} (${req?.url || '/'})`)

        if (activeWs && activeWs.readyState === 1) {
          log('[runner-ws] replacing existing connection')

          try {
            activeWs.close(1000, 'replaced')
          } catch {
            /* ignore */
          }
        }

        activeWs = ws
        emit({ type: 'connected' })

        let lastSeen = Date.now()

        const heartbeatTimer = setInterval(() => {
          if (ws.readyState !== 1) {
            clearInterval(heartbeatTimer)

            return
          }

          if (Date.now() - lastSeen > HEARTBEAT_DEADLINE_MS) {
            log(`[runner-ws] heartbeat deadline (${HEARTBEAT_DEADLINE_MS}ms) exceeded; closing`)

            try {
              ws.close(1011, 'heartbeat-deadline')
            } catch {
              /* ignore */
            }

            clearInterval(heartbeatTimer)

            return
          }

          try {
            ws.send(JSON.stringify({ jsonrpc: JSON_RPC_VERSION, method: 'runner.ping' }))
          } catch (err: any) {
            log(`[runner-ws] heartbeat send failed: ${err.message}`)
          }
        }, HEARTBEAT_INTERVAL_MS)

        ws.on('close', () => clearInterval(heartbeatTimer))

        ws.on('message', data => {
          lastSeen = Date.now()
          handleRunnerMessage(data)
        })

        ws.on('close', code => {
          log(`[runner-ws] runner disconnected code=${code}`)

          if (activeWs === ws) {
            activeWs = null
          }

          rejectAllPending(new Error('Runner disconnected.'))
          emit({ code, type: 'disconnected' })
        })

        ws.on('error', (error: any) => {
          log(`[runner-ws] runner error: ${error?.message || error}`)
          emit({ error, type: 'error' })
        })
      })

      wss.on('error', (error: any) => {
        log(`[runner-ws] server error: ${error?.message || error}`)
      })

      return { path: ipcPath, transport }
    } catch (error: any) {
      await new Promise<void>(resolve => httpServer.close(() => resolve()))
      throw error
    }
  }

  function stop(): Promise<{ ok: boolean }> {
    closed = true
    rejectAllPending(new Error('Runner WS server stopped.'))
    activeWs = null

    if (upgradeHandler) {
      httpServer.removeListener('upgrade', upgradeHandler)
      upgradeHandler = null
    }

    if (typeof (httpServer as any).closeAllConnections === 'function') {
      ;(httpServer as any).closeAllConnections()
    }

    return new Promise(resolve => {
      const finish = () => {
        if (process.platform !== 'win32' && ipcPath) {
          try {
            fs.unlinkSync(ipcPath)
          } catch {
            /* already gone */
          }
        }

        transport = null
        ipcPath = null
        resolve({ ok: true })
      }

      if (wss) {
        wss.close(() => {
          wss = null
          httpServer.close(() => finish())
        })
      } else {
        httpServer.close(() => finish())
      }
    })
  }

  function onEvent(callback: (event: any) => void): () => void {
    emitter.on('event', callback)

    return () => emitter.off('event', callback)
  }

  function getStatus(): RunnerWsStatus {
    return {
      connected: activeWs !== null && activeWs.readyState === 1,
      path: ipcPath,
      pendingCalls: pending.size,
      transport
    }
  }

  return {
    call,
    getStatus,
    onEvent,
    start,
    stop
  }
}
