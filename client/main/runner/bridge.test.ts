import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import { sleep } from '../shared/utils'

import { createRunnerBridge } from './bridge'

function makeFakeProcess() {
  const emitter = new EventEmitter()
  let started = false
  const calls: { startArgs: any } = { startArgs: null }

  const factory = () => ({
    getStatus: () => ({ pid: started ? 9999 : null, running: started }),
    onEvent: (cb: any) => {
      emitter.on('event', cb)

      return () => emitter.off('event', cb)
    },
    restart: async (args: any) => {
      calls.startArgs = args

      return { pid: 9999, running: true } as any
    },
    signalReady: () => emitter.emit('event', { type: 'ready' }),
    start: async (args: any) => {
      calls.startArgs = args
      started = true
      emitter.emit('event', { args: [], command: 'fake', pid: 9999, type: 'start' })

      return { pid: 9999, running: true } as any
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

      return { pid: 9999, running: true } as any
    }
  })

  return { calls, emitter, factory }
}

function makeFakeWsServer() {
  const emitter: any = new EventEmitter()
  let connected = false
  let lastAuthToken: string | null = null

  const factory = ({ authToken }: any = {}) => ({
    call: async (method: string, params: any) => {
      if (method === 'get_tools') {
        return { tools: [{ description: 'A test tool', name: 'test_tool' }] }
      }

      return { method, ok: true, params }
    },
    getStatus: () => ({
      connected,
      path: connected ? emitter.lastPath : null,
      pendingCalls: 0,
      transport: connected ? 'pipe' : null
    }),
    onEvent: (cb: any) => {
      emitter.on('event', cb)

      return () => emitter.off('event', cb)
    },
    start: async ({ path: ipcPath }: any = {}) => {
      connected = true
      lastAuthToken = authToken
      emitter.lastPath = ipcPath
      setTimeout(() => {
        emitter.emit('event', { type: 'connected' })
        emitter.emit('event', { type: 'runner_ready' })
      }, 10)

      return { path: ipcPath, transport: process.platform === 'win32' ? 'pipe' : 'unix' }
    },
    stop: async () => {
      connected = false

      return { ok: true }
    }
  })

  return { emitter, factory, getLastAuthToken: () => lastAuthToken }
}

function makeFakeReverseRpc() {
  const factory = () => async (method: string) => {
    if (method === 'request_llm') {
      return { content: 'fake llm response', usage: null }
    }

    throw new Error(`Unknown method: ${method}`)
  }

  return { factory }
}

function makeBridge(overrides = {}) {
  const process = makeFakeProcess()
  const wsServer = makeFakeWsServer()
  const reverseRpc = makeFakeReverseRpc()

  const bridge = createRunnerBridge({
    log: () => {},
    processFactory: process.factory as any,
    reverseRpcFactory: reverseRpc.factory as any,
    wsServerFactory: wsServer.factory as any,
    ...overrides
  })

  return { bridge, process, reverseRpc, wsServer }
}

async function waitForPhase(bridge: any, phase: string, timeoutMs = 2000): Promise<void> {
  if (bridge.getStatus().phase === phase) {
    return
  }

  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`Timeout waiting for phase '${phase}'`)), timeoutMs)

    const unsub = bridge.onEvent((ev: any) => {
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
    startArgs.endpointPath,
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
  const events: any[] = []
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
  wsServer.emitter.emit('event', { method: 'future.foo', type: 'notification' })

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

  const result: any = await bridge.invoke('test_tool', { x: 1 }, { id: 'c1' })
  assert.equal(result.ok, true)
  assert.equal(result.method, 'execute_tool')
  assert.equal(result.params.name, 'test_tool')
  await bridge.stop()
})

test('start() rolls back when process fails', async () => {
  const process = makeFakeProcess()
  const wsServer = makeFakeWsServer()

  const failingFactory = () => {
    const base = process.factory()

    return {
      ...base,
      waitForReady: async () => {
        throw new Error('not ready')
      }
    }
  }

  const bridge = createRunnerBridge({
    log: () => {},
    processFactory: failingFactory as any,
    wsServerFactory: wsServer.factory as any
  })

  await assert.rejects(bridge.start({ readyTimeoutMs: 2_000 }), (err: any) => /not ready/.test(err.message))
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
  await assert.rejects(bridge.start({ readyTimeoutMs: 2_000 }), (err: any) => /already running/.test(err.message))
  await bridge.stop()
})

test('invoke() throws when runner is not connected', async () => {
  const { bridge } = makeBridge()
  await assert.rejects(bridge.invoke('test_tool', {}), (err: any) => /not connected/.test(err.message))
})
