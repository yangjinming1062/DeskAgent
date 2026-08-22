import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import { sleep } from '../shared/utils'

import { createRunnerBridge } from './bridge'
import type { RunnerBridge, RunnerBridgeEvent, RunnerBridgeStartOptions } from './bridge'
import type { RunnerProcess, RunnerProcessEvent, RunnerProcessStartArgs, RunnerProcessState } from './process'
import type { ReverseRpcOptions } from './reverse-rpc'
import type { CreateRunnerWsServerOptions, RunnerWsEvent, RunnerWsServer } from './rpc-ws'

function makeFakeProcess() {
  const emitter = new EventEmitter()
  let started = false
  const calls: { startArgs: null | RunnerProcessStartArgs } = { startArgs: null }

  const factory: () => RunnerProcess = () => ({
    getStatus: (): RunnerProcessState => ({
      args: null,
      command: null,
      exitCode: null,
      exitSignal: null,
      kind: null,
      lastError: null,
      pid: started ? 9999 : null,
      running: started,
      startedAt: null
    }),
    onEvent: (cb: (event: RunnerProcessEvent) => void) => {
      emitter.on('event', cb)

      return () => emitter.off('event', cb)
    },
    restart: async (args: RunnerProcessStartArgs) => {
      calls.startArgs = args

      return { pid: 9999, running: true } as RunnerProcessState
    },
    signalReady: () => emitter.emit('event', { type: 'ready' }),
    start: async (args: RunnerProcessStartArgs) => {
      calls.startArgs = args
      started = true
      emitter.emit('event', { args: [], command: 'fake', pid: 9999, type: 'start' })

      return { pid: 9999, running: true } as RunnerProcessState
    },
    stop: async () => {
      if (!started) {
        return { noop: true, ok: true }
      }

      started = false
      emitter.emit('event', { code: 0, signal: null, type: 'exit' })

      return { code: 0, ok: true, signal: null }
    },
    waitForReady: async () => {
      await sleep(20)

      return { pid: 9999, running: true } as RunnerProcessState
    }
  })

  return { calls, emitter, factory }
}

function makeFakeWsServer() {
  const emitter: EventEmitter & { lastPath?: string } = new EventEmitter() as EventEmitter & { lastPath?: string }
  let connected = false
  let lastAuthToken: string | null = null

  const factory = ({ authToken }: CreateRunnerWsServerOptions = {}): RunnerWsServer => {
    let lastPath: string | null = null

    return {
      call: async <T = unknown>(method: string, params: Record<string, unknown> = {}) => {
        if (method === 'get_tools') {
          return { tools: [{ description: 'A test tool', name: 'test_tool' }] } as T
        }

        return { method, ok: true, params } as T
      },
      getStatus: () => ({
        connected,
        path: connected ? lastPath : null,
        pendingCalls: 0,
        transport: connected ? 'pipe' : null
      }),
      onEvent: (cb: (event: RunnerWsEvent) => void) => {
        emitter.on('event', cb)

        return () => emitter.off('event', cb)
      },
      start: async ({ path: ipcPath }: { path?: string } = {}) => {
        connected = true
        lastAuthToken = authToken ?? null
        lastPath = ipcPath ?? null
        emitter.lastPath = ipcPath ?? undefined
        setTimeout(() => {
          emitter.emit('event', { type: 'connected' })
          emitter.emit('event', { type: 'runner_ready' })
        }, 10)

        return { path: ipcPath ?? null, transport: process.platform === 'win32' ? 'pipe' : 'unix' }
      },
      stop: async () => {
        connected = false

        return { ok: true }
      }
    }
  }

  return {
    emitter,
    factory,
    getLastAuthToken: () => lastAuthToken
  }
}

function makeFakeReverseRpc() {
  const factory = () => async (method: string) => {
    if (method === 'request_llm') {
      return { content: 'fake llm response', usage: null }
    }

    throw new Error(`Unknown method: ${method}`)
  }

  return { factory: factory as (options: ReverseRpcOptions) => (method: string, params?: unknown) => Promise<unknown> }
}

interface BridgeOverrides {
  log?: (chunk: string) => void
  processFactory?: (args?: RunnerBridgeStartOptions) => RunnerProcess
  reverseRpcFactory?: (options: ReverseRpcOptions) => (method: string, params?: unknown) => Promise<unknown>
  spiritagentHome?: null | string
  wsServerFactory?: (options: CreateRunnerWsServerOptions) => RunnerWsServer
}

function makeBridge(overrides: BridgeOverrides = {}) {
  const process = makeFakeProcess()
  const wsServer = makeFakeWsServer()
  const reverseRpc = makeFakeReverseRpc()

  const bridge = createRunnerBridge({
    log: () => {},
    processFactory: process.factory,
    reverseRpcFactory: reverseRpc.factory,
    wsServerFactory: wsServer.factory,
    ...overrides
  })

  return { bridge, process, reverseRpc, wsServer }
}

async function waitForPhase(bridge: RunnerBridge, phase: string, timeoutMs = 2000): Promise<void> {
  if (bridge.getStatus().phase === phase) {
    return
  }

  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`Timeout waiting for phase '${phase}'`)), timeoutMs)

    const unsub = bridge.onEvent((ev: RunnerBridgeEvent) => {
      if (ev.type === 'running' && phase === 'running') {
        clearTimeout(timer)
        unsub()
        resolve()
      }

      if (ev.type === 'error') {
        clearTimeout(timer)
        unsub()
        reject(ev.error)
      }

      if (ev.type === 'stopped' && phase === 'stopped') {
        clearTimeout(timer)
        unsub()
        resolve()
      }
    })
  })
}

test('start() composes process + ws-server and reaches "running" phase', async () => {
  const { bridge } = makeBridge()

  bridge.start({ readyTimeoutMs: 2_000 })
  await waitForPhase(bridge, 'running')

  const status = bridge.getStatus()
  assert.equal(status.phase, 'running')
  assert.equal(status.wsServer?.connected, true)
  assert.equal(status.runner?.pid, 9999)
  assert.match(
    status.wsServer?.path || '',
    process.platform === 'win32' ? /\\\\\.\\pipe\\spiritagent-runner-\d+/ : /runner-\d+\.sock$/
  )
  await bridge.stop()
})

test('start() spawns the runner with the endpoint argv contract', async () => {
  const { bridge, process: processMock, wsServer } = makeBridge()

  bridge.start({ readyTimeoutMs: 2_000 })
  await waitForPhase(bridge, 'running')

  const startArgs = processMock.calls.startArgs
  assert.ok(startArgs, 'process.start() was never invoked')
  assert.equal(startArgs.authToken, wsServer.getLastAuthToken(), 'argv token must match the ws server gate')
  assert.match(
    startArgs.endpointPath || '',
    process.platform === 'win32' ? /\\\\\.\\pipe\\spiritagent-runner-\d+/ : /runner-\d+\.sock$/
  )
  await bridge.stop()
})

test('start() writes the endpoint file with the IPC schema when spiritagentHome is set', async () => {
  const spiritagentHome = fs.mkdtempSync(path.join(os.tmpdir(), 'spiritagent-bridge-test-'))
  const { bridge, wsServer } = makeBridge({ spiritagentHome })

  bridge.start({ readyTimeoutMs: 2_000 })
  await waitForPhase(bridge, 'running')

  const endpointFile = path.join(spiritagentHome, 'desktop-endpoint.json')
  const payload = JSON.parse(fs.readFileSync(endpointFile, 'utf8'))
  assert.equal(payload.transport, process.platform === 'win32' ? 'pipe' : 'unix')
  assert.equal(payload.path, wsServer.emitter.lastPath)
  assert.equal(payload.pid, process.pid)
  assert.equal(payload.token, wsServer.getLastAuthToken())
  assert.equal(typeof payload.timestamp, 'number')

  await bridge.stop()
  assert.equal(fs.existsSync(endpointFile), false, 'stop() must remove the endpoint file')
  fs.rmSync(spiritagentHome, { force: true, recursive: true })
})

test('start() fetches tools via get_tools RPC after runner_ready', async () => {
  const { bridge } = makeBridge()

  bridge.start({ readyTimeoutMs: 2_000 })
  await waitForPhase(bridge, 'running')

  const tools = bridge.getTools()
  assert.equal(tools.length, 1)
  assert.equal(tools[0].name, 'test_tool')
  await bridge.stop()
})

test('start() forwards runner_ready and running events', async () => {
  const { bridge } = makeBridge()
  const events: string[] = []
  bridge.onEvent(ev => events.push(ev.type))

  bridge.start({ readyTimeoutMs: 2_000 })
  await waitForPhase(bridge, 'running')

  assert.ok(events.includes('running'))
  await bridge.stop()
})

test('swallows the runner-rpc-ws `connected` lifecycle event', async () => {
  const logs: string[] = []
  const { bridge, wsServer } = makeBridge({ log: (msg: string) => logs.push(msg) })

  bridge.start({ readyTimeoutMs: 2_000 })
  await waitForPhase(bridge, 'running')

  wsServer.emitter.emit('event', { type: 'connected' })
  wsServer.emitter.emit('event', { method: 'future.foo', params: {}, type: 'notification' })

  const unhandled = logs.filter(line => /unhandled ws server event/.test(line))
  assert.equal(unhandled.length, 1)
  assert.match(unhandled[0], /notification future\.foo/)
  assert.ok(!logs.some(line => /unhandled.*connected/.test(line)))

  await bridge.stop()
})

test('invoke() forwards to ws-server.call()', async () => {
  const { bridge } = makeBridge()

  bridge.start({ readyTimeoutMs: 2_000 })
  await waitForPhase(bridge, 'running')

  const result = await bridge.invoke<{ method: string; ok: boolean; params: { name: string } }>(
    'test_tool',
    { x: 1 },
    { id: 'c1' }
  )

  assert.equal(result.ok, true)
  assert.equal(result.method, 'execute_tool')
  assert.equal(result.params.name, 'test_tool')
  await bridge.stop()
})

test('start() rolls back when process fails', async () => {
  const process = makeFakeProcess()
  const wsServer = makeFakeWsServer()

  const baseFactory = process.factory

  const failingFactory: (args?: RunnerBridgeStartOptions) => RunnerProcess = () => {
    const base = baseFactory()

    return {
      ...base,
      waitForReady: async () => {
        throw new Error('not ready')
      }
    }
  }

  const bridge = createRunnerBridge({
    log: () => {},
    processFactory: failingFactory,
    wsServerFactory: wsServer.factory
  })

  await assert.rejects(bridge.start({ readyTimeoutMs: 2_000 }), (err: unknown) =>
    err instanceof Error ? /not ready/.test(err.message) : false
  )
  assert.equal(bridge.getStatus().phase, 'error')
})

test('stop() closes ws-server and stops process', async () => {
  const { bridge } = makeBridge()

  await bridge.start({ readyTimeoutMs: 2_000 })
  const result = await bridge.stop({ reason: 'logout' })
  assert.equal(result.ok, true)
  assert.equal(bridge.getStatus().phase, 'stopped')
})

test('stop() is a noop when never started', async () => {
  const { bridge } = makeBridge()
  const result = await bridge.stop()
  assert.equal(result.ok, true)
  assert.equal(result.noop, true)
})

test('start() rejects second start while running', async () => {
  const { bridge } = makeBridge()
  bridge.start({ readyTimeoutMs: 2_000 })
  await waitForPhase(bridge, 'running')
  await assert.rejects(bridge.start({ readyTimeoutMs: 2_000 }), (err: unknown) =>
    err instanceof Error ? /already running/.test(err.message) : false
  )
  await bridge.stop()
})

test('invoke() throws when runner is not connected', async () => {
  const { bridge } = makeBridge()
  await assert.rejects(bridge.invoke('test_tool', {}), (err: unknown) =>
    err instanceof Error ? /not connected/.test(err.message) : false
  )
})
