const { EventEmitter } = require('node:events')
const fs = require('node:fs')
const path = require('node:path')

const { atomicWriteFile } = require('../shared/utils.cjs')

/**
 * Orchestrator tying together Runner process, local WS server, and reverse RPC.
 */

function createRunnerBridge(options = {}) {
  const log = typeof options.log === 'function' ? options.log : () => {}
  const emit = new EventEmitter()
  const onEvent = callback => {
    emit.on('event', callback)
    return () => emit.off('event', callback)
  }

  const processFactory = options.processFactory || null
  const wsServerFactory = options.wsServerFactory || null
  const reverseRpcFactory = options.reverseRpcFactory || null

  let runnerProcess = null
  let wsServer = null
  let cachedTools = null
  let subUnsubFns = []
  let endpointFilePath = null
  let state = {
    phase: 'idle',
    startedAt: null,
    stoppedAt: null,
    lastError: null,
    // Runner-probed capability flags, surfaced to the renderer for UI branching.
    capabilities: null,
    runnerVersion: null,
    probeFailed: null
  }

  function setState(patch) {
    state = { ...state, ...patch }
  }

  function getStatus() {
    return {
      ...state,
      runner: runnerProcess?.getStatus?.() ?? null,
      wsServer: wsServer?.getStatus?.() ?? null
    }
  }

  function fail(phase, error) {
    const err = error instanceof Error ? error : new Error(String(error))
    setState({ phase, lastError: err.message })
    emit.emit('event', { type: 'error', phase, error: err })
    // Also emit 'stopped' on error so the renderer shows the recovery surface.
    if (phase === 'error') {
      emit.emit('event', { type: 'stopped', reason: err.message, errors: [err.message] })
    }
    return err
  }

  function detachSubs() {
    for (const off of subUnsubFns) {
      try {
        off()
      } catch {
        /* already detached */
      }
    }
    subUnsubFns = []
  }

  async function writeEndpointFile(port) {
    const deskagentHome = options.deskagentHome
    if (!deskagentHome) return
    endpointFilePath = path.join(deskagentHome, 'desktop-endpoint.json')
    const payload = JSON.stringify({ port, pid: process.pid, timestamp: Date.now() })
    try {
      await atomicWriteFile(endpointFilePath, payload)
      log(`[runner-bridge] wrote endpoint file: port=${port} pid=${process.pid}`)
    } catch (error) {
      log(`[runner-bridge] failed to write endpoint file: ${error.message}`)
    }
  }

  function cleanupEndpointFile() {
    if (!endpointFilePath) return
    try {
      fs.unlinkSync(endpointFilePath)
      log('[runner-bridge] cleaned up endpoint file')
    } catch (error) {
      if (error?.code !== 'ENOENT') {
        log(`[runner-bridge] failed to cleanup endpoint file: ${error.message}`)
      }
    }
    endpointFilePath = null
  }

  async function rollback(reason) {
    const tasks = []
    if (wsServer) tasks.push(wsServer.stop({ reason }).catch(e => e))
    if (runnerProcess) tasks.push(runnerProcess.stop({ reason }).catch(e => e))
    await Promise.all(tasks)
    // Endpoint file may be on disk from a successful wsServer.start() that
    // was followed by a runner start failure — clean it so the next Runner
    // reconnect doesn't read a dead port. cleanupEndpointFile is idempotent.
    cleanupEndpointFile()
    wsServer = null
    runnerProcess = null
    cachedTools = null
  }

  async function start(args = {}) {
    detachSubs()

    if (state.phase === 'starting' || state.phase === 'running' || state.phase === 'stopping') {
      throw new Error('Runner bridge is already running.')
    }

    setState({
      phase: 'starting',
      startedAt: Date.now(),
      stoppedAt: null,
      lastError: null
    })

    const processInstance = processFactory ? processFactory(args) : null
    if (!processInstance) {
      throw fail('error', new Error('No runner process factory wired.'))
    }
    runnerProcess = processInstance

    const offProcess = runnerProcess.onEvent?.(ev => {
      if (ev.type === 'exit') {
        if (state.phase === 'running') {
          fail('stopped', new Error(`Runner exited (code=${ev.code}, signal=${ev.signal})`))
        } else if (state.phase === 'starting' || state.phase === 'stopping') {
          fail('error', new Error(`Runner exited during ${state.phase} (code=${ev.code}, signal=${ev.signal})`))
        }
      }
    })
    if (typeof offProcess === 'function') subUnsubFns.push(offProcess)

    wsServer = wsServerFactory
      ? wsServerFactory({
          onReverseRpc: reverseRpcFactory ? reverseRpcFactory({ backendSession: args.backendSession, log }) : null,
          log: options.log
        })
      : null
    if (!wsServer) {
      await rollback('ws-server-init')
      throw fail('error', new Error('No WS server factory wired.'))
    }

    const offWs = wsServer.onEvent?.(ev => {
      if (ev.type === 'runner_ready') {
        handleRunnerReady(ev)
      } else if (ev.type === 'tools_changed') {
        handleToolsChanged()
      } else if (ev.type === 'disconnected') {
        if (state.phase === 'running') {
          fail('stopped', new Error('Runner disconnected from WS server.'))
        }
      } else if (ev.type === 'error') {
        log(`[runner-bridge] ws server error: ${ev.error?.message || ev.error}`)
      } else if (ev.type === 'connected') {
        // ``runner-rpc-ws.cjs`` emits ``connected`` on every successful
        // handshake (initial + reconnect). The bridge only needs ``runner_ready``
        // (sent right after) to advance to ``running``, so just no-op.
      } else {
        // ``runner-rpc-ws.cjs`` forwards any Runner notification that is not
        // ``runner_ready`` as ``{type: 'notification', method, params}``.
        // Today nothing in the bridge consumes additional notifications, but
        // silently dropping future protocol additions would hide mismatches
        // from operators — surface them so the boot log shows them.
        const detail = ev.type === 'notification' ? `notification ${ev.method}` : ev.type
        log(`[runner-bridge] unhandled ws server event: ${detail}`)
      }
    })
    if (typeof offWs === 'function') subUnsubFns.push(offWs)

    try {
      const { port } = await wsServer.start()
      log(`[runner-bridge] WS server listening on port ${port}`)
      await writeEndpointFile(port)
    } catch (error) {
      await rollback('ws-server-start')
      throw fail('error', error)
    }

    const wsStatus = wsServer.getStatus()
    const desktopWs = `ws://127.0.0.1:${wsStatus.port}/rpc`
    try {
      await runnerProcess.start({ desktopWs, executable: args.executable })
    } catch (error) {
      await rollback('process-start')
      throw fail('error', error)
    }

    try {
      await runnerProcess.waitForReady({ timeoutMs: args.readyTimeoutMs ?? 8_000 })
    } catch (error) {
      await rollback('ready-timeout')
      throw fail('error', error)
    }

    return getStatus()
  }

  async function handleRunnerReady(payload) {
    log('[runner-bridge] runner_ready received')

    if (runnerProcess?.signalReady) {
      runnerProcess.signalReady()
    }

    // Stash the runner-probed capabilities so the renderer can decide
    // Stash runner-probed capabilities so the renderer can branch on local-runtime support.
    if (payload && typeof payload === 'object') {
      setState({
        capabilities: payload.capabilities ?? null,
        runnerVersion: payload.version ?? null,
        probeFailed: payload.probe_failed ?? null
      })
    }

    if (state.phase !== 'starting') return

    await _fetchAndCacheTools()
    if (state.phase !== 'starting') return

    setState({ phase: 'running' })
    emit.emit('event', {
      type: 'running',
      tools: cachedTools,
      capabilities: state.capabilities,
      runnerVersion: state.runnerVersion,
      probeFailed: state.probeFailed
    })
  }

  // Debounce tools_changed — MCP discovery can fire back-to-back; 300ms
  // coalesces them into a single re-fetch + emit.
  let _toolsChangedDebounce = null
  async function handleToolsChanged() {
    log('[runner-bridge] tools_changed received')
    if (_toolsChangedDebounce) clearTimeout(_toolsChangedDebounce)
    _toolsChangedDebounce = setTimeout(async () => {
      _toolsChangedDebounce = null
      await _fetchAndCacheTools()
      // Always emit regardless of phase: tools_changed arrives after we're
      // in the `running` phase (background MCP discovery completed
      // post-startup), so the renderer's `runner_status: tools_changed`
      // listener re-syncs the new schemas to backend without needing a
      // phase transition.
      emit.emit('event', { type: 'tools_changed', tools: cachedTools })
    }, 300)
  }

  async function _fetchAndCacheTools() {
    try {
      const result = await wsServer.call('get_tools', {}, { timeoutMs: 10_000 })
      cachedTools = result?.tools || []
      log(`[runner-bridge] got ${cachedTools.length} tools from runner`)
      if (cachedTools.length > 0) {
        const names = cachedTools.map(t => t?.function?.name || t?.name).filter(Boolean)
        log(`[runner-bridge] tool names: ${names.join(', ') || '(unparseable schemas)'}`)
        log(
          `[runner-bridge] has read_file: ${names.includes('read_file')} | has list_directory: ${names.includes('list_directory')}`
        )
      } else {
        log(`[runner-bridge] warning: 0 tools — Runner is up but advertises no schema; LLM will have no file tools`)
      }
    } catch (error) {
      log(`[runner-bridge] get_tools failed: ${error.message}`)
      log(`[runner-bridge] error.stack: ${error.stack || '(no stack)'}`)
      cachedTools = []
    }
  }

  async function stop({ reason } = {}) {
    if (state.phase === 'idle' || state.phase === 'stopped') {
      return { ok: true, noop: true }
    }
    setState({ phase: 'stopping' })
    log(`[runner-bridge] stop reason=${reason || 'unspecified'}`)

    const errors = []
    const tasks = []
    if (wsServer)
      tasks.push(
        wsServer.stop().catch(e => {
          errors.push(e)
        })
      )
    if (runnerProcess)
      tasks.push(
        runnerProcess.stop({ reason: reason || 'desktop-stop' }).catch(e => {
          errors.push(e)
        })
      )
    await Promise.all(tasks)

    cleanupEndpointFile()
    detachSubs()
    wsServer = null
    runnerProcess = null
    cachedTools = null
    setState({ phase: 'stopped', stoppedAt: Date.now() })
    emit.emit('event', { type: 'stopped', reason, errors: errors.map(e => e?.message || String(e)) })
    return { ok: errors.length === 0, errors: errors.map(e => e?.message || String(e)) }
  }

  async function _rpc(method, params, opts = {}) {
    if (!wsServer || !wsServer.getStatus()?.connected) {
      throw new Error('Runner is not connected.')
    }
    return wsServer.call(method, params || {}, opts)
  }

  // Registered-tool dispatch: backend's `tool.call` event names a tool by its
  // registry name; the runner's `execute_tool` RPC wraps it as
  // `{name, args}`.
  const invoke = (name, args, opts) => _rpc('execute_tool', { name, args: args || {} }, opts)

  // First-class JSON-RPC pass-through for methods the runner implements
  // directly (e.g. `mcp.reload`) without the `execute_tool` wrapper.
  const dispatch = (method, params, opts) => _rpc(method, params, opts)

  function getTools() {
    return cachedTools || []
  }

  return {
    start,
    stop,
    invoke,
    dispatch,
    getTools,
    getStatus,
    onEvent
  }
}

module.exports = {
  createRunnerBridge
}
