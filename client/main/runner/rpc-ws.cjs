const { EventEmitter } = require('node:events')
const http = require('node:http')

// Matches Runner's `request_llm` default floor (`runner/server.py:60`, 120s)
// and the local reverse-RPC proxy (`runner-reverse-rpc.cjs:55`, 120_000).
// Keeping all three at 120s prevents the desktop from timing out a call that
// the runner is still executing — the runner would otherwise keep a future
// alive past the desktop-side rejection, leaking the IPC future in
// `core/ipc.py::_pending` until the 300s generic IPC timeout kicks in.
const DEFAULT_TIMEOUT_MS = 120_000
const JSON_RPC_VERSION = '2.0'

// Application-level heartbeat — OS keepalive alone can take minutes to
// surface a network-isolated runner. Ping every 10s; no frame within 120s
// (matching the runner's LLM floor) means dead. Any inbound frame resets
// the deadline.
const HEARTBEAT_INTERVAL_MS = 10_000
const HEARTBEAT_DEADLINE_MS = 120_000

function createRunnerWsServer(options = {}) {
  const log = typeof options.log === 'function' ? options.log : () => {}
  const onReverseRpc = typeof options.onReverseRpc === 'function' ? options.onReverseRpc : null
  const emitter = new EventEmitter()
  const pending = new Map()
  const httpServer = http.createServer()
  let wss = null
  let activeWs = null
  let nextId = 1
  let port = 0
  let closed = false

  function emit(event) {
    emitter.emit('event', event)
  }

  function rejectAllPending(error) {
    for (const [id, entry] of pending.entries()) {
      clearTimeout(entry.timer)
      entry.reject(error)
      pending.delete(id)
    }
  }

  function sendToRunner(payload) {
    if (!activeWs || activeWs.readyState !== 1) return false
    try {
      activeWs.send(JSON.stringify(payload))
      return true
    } catch (error) {
      log(`[runner-ws] send failed: ${error.message}`)
      return false
    }
  }

  function handleRunnerMessage(raw) {
    let message
    try {
      message = JSON.parse(typeof raw === 'string' ? raw : raw.toString())
    } catch (error) {
      log(`[runner-ws] parse error: ${error.message}`)
      return
    }

    if (!message || typeof message !== 'object') return

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
        // Forward the runner's probed capabilities/flags to the bridge.
        emit({
          type: 'runner_ready',
          capabilities: message.params?.capabilities ?? null,
          version: message.params?.version ?? null,
          probe_failed: message.params?.probe_failed ?? null
        })
      } else if (method === 'tools_changed') {
        // Runner re-registered tools (e.g. MCP discovery finished after
        // startup). The bridge re-fetches schemas and re-syncs to backend.
        emit({ type: 'tools_changed' })
      } else {
        emit({ type: 'notification', method, params: message.params || {} })
      }
      return
    }
  }

  async function handleReverseRpc(id, method, params) {
    if (!onReverseRpc) {
      sendToRunner({
        jsonrpc: JSON_RPC_VERSION,
        id,
        error: { code: -32601, message: `No handler for reverse RPC: ${method}` }
      })
      return
    }

    try {
      const result = await onReverseRpc(method, params)
      sendToRunner({ jsonrpc: JSON_RPC_VERSION, id, result })
    } catch (error) {
      sendToRunner({
        jsonrpc: JSON_RPC_VERSION,
        id,
        error: { code: -32000, message: error?.message || String(error) }
      })
    }
  }

  function call(method, params = {}, { id: explicitId, timeoutMs } = {}) {
    if (closed) {
      return Promise.reject(new Error('Runner WS server is closed.'))
    }
    const id = explicitId !== undefined ? String(explicitId) : `call_${nextId++}`
    const effectiveTimeoutMs = Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : DEFAULT_TIMEOUT_MS

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (!pending.has(id)) return
        pending.delete(id)
        reject(new Error(`Runner call '${method}' (id=${id}) timed out after ${effectiveTimeoutMs}ms.`))
      }, effectiveTimeoutMs)

      pending.set(id, { resolve, reject, timer, method })

      const sent = sendToRunner({
        jsonrpc: JSON_RPC_VERSION,
        id,
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

  function start() {
    if (wss) return Promise.resolve({ port })

    return new Promise((resolve, reject) => {
      httpServer.listen(0, '127.0.0.1', () => {
        port = httpServer.address()?.port || 0
        log(`[runner-ws] listening on 127.0.0.1:${port}`)

        try {
          const WebSocket = require('ws')
          wss = new WebSocket.Server({ server: httpServer })

          wss.on('connection', (ws, req) => {
            const clientAddr = req.socket?.remoteAddress || 'unknown'
            log(`[runner-ws] runner connected from ${clientAddr}`)

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

            // Any inbound frame resets the 120s deadline so a busy runner
            // isn't misclassified as dead; drop a stuck connection with 1011.
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
              } catch (err) {
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
              if (activeWs === ws) activeWs = null
              rejectAllPending(new Error('Runner disconnected.'))
              emit({ type: 'disconnected', code })
            })

            ws.on('error', error => {
              log(`[runner-ws] runner error: ${error.message}`)
              emit({ type: 'error', error })
            })
          })

          wss.on('error', error => {
            log(`[runner-ws] server error: ${error.message}`)
            reject(error)
          })

          resolve({ port })
        } catch (error) {
          httpServer.close()
          reject(error)
        }
      })

      httpServer.on('error', error => {
        log(`[runner-ws] http server error: ${error.message}`)
        reject(error)
      })
    })
  }

  function stop() {
    closed = true
    rejectAllPending(new Error('Runner WS server stopped.'))
    activeWs = null

    return new Promise(resolve => {
      if (wss) {
        wss.close(() => {
          wss = null
          httpServer.close(() => resolve({ ok: true }))
        })
      } else {
        httpServer.close(() => resolve({ ok: true }))
      }
    })
  }

  function onEvent(callback) {
    emitter.on('event', callback)
    return () => emitter.off('event', callback)
  }

  function getStatus() {
    return {
      connected: activeWs !== null && activeWs.readyState === 1,
      port,
      pendingCalls: pending.size
    }
  }

  return {
    start,
    stop,
    call,
    onEvent,
    getStatus
  }
}

module.exports = {
  createRunnerWsServer,
  DEFAULT_TIMEOUT_MS,
  JSON_RPC_VERSION
}
