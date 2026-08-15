import crypto from 'node:crypto'
import { EventEmitter } from 'node:events'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import type { RunnerCapabilities } from '../../renderer/shared/types/global'
import { atomicWriteFile } from '../shared/utils'

import type { RunnerProcess } from './process'
import type { RunnerWsServer } from './rpc-ws'

// macOS sun_path is capped at 104 bytes; stay under it with a margin.
export const MAC_SOCK_PATH_BYTE_LIMIT = 100

export function computeDesktopEndpoint(deskagentHome?: null | string): { path: string; transport: string } {
  if (process.platform === 'win32') {
    return { path: `\\\\.\\pipe\\deskagent-runner-${process.pid}`, transport: 'pipe' }
  }

  const home = deskagentHome || os.tmpdir()
  const primary = path.join(home, `runner-${process.pid}.sock`)

  if (Buffer.byteLength(primary) <= MAC_SOCK_PATH_BYTE_LIMIT) {
    return { path: primary, transport: 'unix' }
  }

  const digest = crypto.createHash('sha256').update(`${home}|${process.pid}`).digest('hex').slice(0, 8)
  const uid = typeof process.getuid === 'function' ? process.getuid() : 0

  return { path: path.join(os.tmpdir(), `deskagent-${uid}-${digest}.sock`), transport: 'unix' }
}

export function sweepLegacySockets(deskagentHome: string): void {
  try {
    for (const name of fs.readdirSync(deskagentHome)) {
      if (/^runner-\d+\.sock$/.test(name)) {
        try {
          fs.unlinkSync(path.join(deskagentHome, name))
        } catch {
          /* raced away */
        }
      }
    }
  } catch {
    /* deskagentHome missing/unreadable — nothing to sweep */
  }
}

export interface RunnerBridgeOptions {
  deskagentHome?: null | string
  log?: (chunk: string) => void
  processFactory?: null | ((args?: any) => RunnerProcess)
  pushConfig?: null | ((config?: Record<string, any>) => Promise<any> | void)
  reverseRpcFactory?: null | ((options: { backendSession: any; log: any }) => any)
  wsServerFactory?: null | ((options: { authToken: string; log: any; onReverseRpc: any }) => RunnerWsServer)
}

export interface RunnerBridgeState {
  capabilities: null | RunnerCapabilities
  lastError: null | string
  phase: 'error' | 'idle' | 'running' | 'starting' | 'stopped' | 'stopping'
  probeFailed: boolean | null
  runnerVersion: null | string
  startedAt: null | number
  stoppedAt: null | number
}

export interface RunnerBridgeStatus extends RunnerBridgeState {
  runner: any
  wsServer: any
}

export interface RunnerBridge {
  dispatch: <T = unknown>(method: string, params?: Record<string, unknown>, opts?: any) => Promise<T>
  getStatus: () => RunnerBridgeStatus
  getTools: () => any[]
  invoke: <T = unknown>(name: string, args?: Record<string, unknown>, opts?: any) => Promise<T>
  onEvent: (callback: (event: any) => void) => () => void
  start: (args?: any) => Promise<RunnerBridgeStatus>
  stop: (options?: { reason?: string }) => Promise<{ errors?: string[]; noop?: boolean; ok: boolean }>
}

export function createRunnerBridge(options: RunnerBridgeOptions = {}): RunnerBridge {
  const log = typeof options.log === 'function' ? options.log : () => {}
  const emit = new EventEmitter()

  const onEvent = (callback: (event: any) => void) => {
    emit.on('event', callback)

    return () => emit.off('event', callback)
  }

  const processFactory = options.processFactory || null
  const wsServerFactory = options.wsServerFactory || null
  const reverseRpcFactory = options.reverseRpcFactory || null
  const pushConfig = typeof options.pushConfig === 'function' ? options.pushConfig : null

  let runnerProcess: null | RunnerProcess = null
  let wsServer: null | RunnerWsServer = null
  let cachedTools: any[] | null = null
  let subUnsubFns: Array<() => void> = []
  let endpointFilePath: null | string = null

  let state: RunnerBridgeState = {
    capabilities: null,
    lastError: null,
    phase: 'idle',
    probeFailed: null,
    runnerVersion: null,
    startedAt: null,
    stoppedAt: null
  }

  function setState(patch: Partial<RunnerBridgeState>): void {
    state = { ...state, ...patch }
  }

  function getStatus(): RunnerBridgeStatus {
    return {
      ...state,
      runner: runnerProcess?.getStatus?.() ?? null,
      wsServer: wsServer?.getStatus?.() ?? null
    }
  }

  function fail(phase: 'error' | 'stopped', error: unknown): Error {
    const err = error instanceof Error ? error : new Error(String(error))
    setState({ lastError: err.message, phase })
    emit.emit('event', { error: err, phase, type: 'error' })

    if (phase === 'error') {
      emit.emit('event', { errors: [err.message], reason: err.message, type: 'stopped' })
    }

    return err
  }

  function detachSubs(): void {
    for (const off of subUnsubFns) {
      try {
        off()
      } catch {
        /* already detached */
      }
    }

    subUnsubFns = []
  }

  async function writeEndpointFile(endpoint: { path: string; token: string; transport: string }): Promise<void> {
    const deskagentHome = options.deskagentHome

    if (!deskagentHome) {
      return
    }

    endpointFilePath = path.join(deskagentHome, 'desktop-endpoint.json')

    const payload = JSON.stringify({
      path: endpoint.path,
      pid: process.pid,
      timestamp: Date.now(),
      token: endpoint.token,
      transport: endpoint.transport
    })

    try {
      await atomicWriteFile(endpointFilePath, payload)
      log(
        `[runner-bridge] wrote endpoint file: transport=${endpoint.transport} path=${endpoint.path} pid=${process.pid}`
      )
    } catch (error: any) {
      log(`[runner-bridge] failed to write endpoint file: ${error.message}`)
    }
  }

  function cleanupEndpointFile(): void {
    if (!endpointFilePath) {
      return
    }

    try {
      fs.unlinkSync(endpointFilePath)
      log('[runner-bridge] cleaned up endpoint file')
    } catch (error: any) {
      if (error?.code !== 'ENOENT') {
        log(`[runner-bridge] failed to cleanup endpoint file: ${error.message}`)
      }
    }

    endpointFilePath = null
  }

  async function rollback(reason: string): Promise<void> {
    const tasks: Promise<any>[] = []

    if (wsServer) {
      tasks.push(wsServer.stop().catch(e => e))
    }

    if (runnerProcess) {
      tasks.push(runnerProcess.stop({ reason }).catch(e => e))
    }

    await Promise.all(tasks)
    cleanupEndpointFile()
    wsServer = null
    runnerProcess = null
    cachedTools = null
  }

  async function start(args: any = {}): Promise<RunnerBridgeStatus> {
    detachSubs()

    if (state.phase === 'starting' || state.phase === 'running' || state.phase === 'stopping') {
      throw new Error('Runner bridge is already running.')
    }

    setState({
      lastError: null,
      phase: 'starting',
      startedAt: Date.now(),
      stoppedAt: null
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

    if (typeof offProcess === 'function') {
      subUnsubFns.push(offProcess)
    }

    const authToken = crypto.randomBytes(32).toString('hex')
    const endpoint = computeDesktopEndpoint(options.deskagentHome)

    if (process.platform !== 'win32' && options.deskagentHome) {
      sweepLegacySockets(options.deskagentHome)
    }

    wsServer = wsServerFactory
      ? wsServerFactory({
          authToken,
          log: options.log,
          onReverseRpc: reverseRpcFactory ? reverseRpcFactory({ backendSession: args.backendSession, log }) : null
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
        // Connected event
      } else {
        const detail = ev.type === 'notification' ? `notification ${ev.method}` : ev.type
        log(`[runner-bridge] unhandled ws server event: ${detail}`)
      }
    })

    if (typeof offWs === 'function') {
      subUnsubFns.push(offWs)
    }

    try {
      const started = await wsServer.start({ path: endpoint.path })
      log(
        `[runner-bridge] WS server listening on ${started?.transport || endpoint.transport} ${started?.path || endpoint.path}`
      )
      await writeEndpointFile({ ...endpoint, token: authToken })
    } catch (error) {
      await rollback('ws-server-start')
      throw fail('error', error)
    }

    try {
      await runnerProcess.start({ authToken, endpointPath: endpoint.path, executable: args.executable })
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

  async function handleRunnerReady(payload: any): Promise<void> {
    log('[runner-bridge] runner_ready received')

    if (runnerProcess?.signalReady) {
      runnerProcess.signalReady()
    }

    if (payload && typeof payload === 'object') {
      setState({
        capabilities: payload.capabilities ?? null,
        probeFailed: payload.probe_failed ?? null,
        runnerVersion: payload.version ?? null
      })
    }

    if (state.phase !== 'starting') {
      if (pushConfig) {
        Promise.resolve(pushConfig()).catch(err =>
          log(`[runner-bridge] config push on reconnect failed: ${err.message}`)
        )
      }

      emit.emit('event', {
        capabilities: state.capabilities,
        probeFailed: state.probeFailed,
        runnerVersion: state.runnerVersion,
        tools: cachedTools,
        type: 'runner_ready'
      })

      return
    }

    if (pushConfig) {
      try {
        await pushConfig()
      } catch (err: any) {
        log(`[runner-bridge] initial config push failed: ${err.message}`)
      }
    }

    await _fetchAndCacheTools()

    if (state.phase !== 'starting') {
      return
    }

    setState({ phase: 'running' })
    emit.emit('event', {
      capabilities: state.capabilities,
      probeFailed: state.probeFailed,
      runnerVersion: state.runnerVersion,
      tools: cachedTools,
      type: 'running'
    })
  }

  let _toolsChangedDebounce: NodeJS.Timeout | null = null

  async function handleToolsChanged(): Promise<void> {
    log('[runner-bridge] tools_changed received')

    if (_toolsChangedDebounce) {
      clearTimeout(_toolsChangedDebounce)
    }

    _toolsChangedDebounce = setTimeout(async () => {
      _toolsChangedDebounce = null
      await _fetchAndCacheTools()
      emit.emit('event', { tools: cachedTools, type: 'tools_changed' })
    }, 300)
  }

  async function _fetchAndCacheTools(): Promise<void> {
    try {
      const result = await wsServer!.call<{ tools?: any[] }>('get_tools', {}, { timeoutMs: 10_000 })
      cachedTools = result?.tools || []
      log(`[runner-bridge] got ${cachedTools.length} tools from runner`)

      if (cachedTools.length > 0) {
        const names = cachedTools.map(t => t?.function?.name || t?.name).filter(Boolean)
        log(`[runner-bridge] tool names: ${names.join(', ') || '(unparseable schemas)'}`)
      }
    } catch (error: any) {
      log(`[runner-bridge] get_tools failed: ${error.message}`)
      cachedTools = []
    }
  }

  async function stop({ reason }: { reason?: string } = {}): Promise<{
    errors?: string[]
    noop?: boolean
    ok: boolean
  }> {
    if (state.phase === 'idle' || state.phase === 'stopped') {
      return { noop: true, ok: true }
    }

    setState({ phase: 'stopping' })
    log(`[runner-bridge] stop reason=${reason || 'unspecified'}`)

    const errors: any[] = []
    const tasks: Promise<any>[] = []

    if (wsServer) {
      tasks.push(
        wsServer.stop().catch(e => {
          errors.push(e)
        })
      )
    }

    if (runnerProcess) {
      tasks.push(
        runnerProcess.stop({ reason: reason || 'desktop-stop' }).catch(e => {
          errors.push(e)
        })
      )
    }

    await Promise.all(tasks)

    cleanupEndpointFile()
    detachSubs()
    wsServer = null
    runnerProcess = null
    cachedTools = null
    setState({ phase: 'stopped', stoppedAt: Date.now() })
    emit.emit('event', { errors: errors.map(e => e?.message || String(e)), reason, type: 'stopped' })

    return { errors: errors.map(e => e?.message || String(e)), ok: errors.length === 0 }
  }

  async function _rpc<T = unknown>(method: string, params: Record<string, unknown>, opts: any = {}): Promise<T> {
    if (!wsServer || !wsServer.getStatus()?.connected) {
      throw new Error('Runner is not connected.')
    }

    return wsServer.call<T>(method, params || {}, opts)
  }

  const invoke = <T = unknown>(name: string, args?: Record<string, unknown>, opts?: any): Promise<T> =>
    _rpc<T>('execute_tool', { args: args || {}, name }, opts)

  const dispatch = <T = unknown>(method: string, params?: Record<string, unknown>, opts?: any): Promise<T> =>
    _rpc<T>(method, params || {}, opts)

  function getTools(): any[] {
    return cachedTools || []
  }

  return {
    dispatch,
    getStatus,
    getTools,
    invoke,
    onEvent,
    start,
    stop
  }
}
