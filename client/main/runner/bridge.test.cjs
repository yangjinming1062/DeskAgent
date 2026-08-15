const test = require('node:test')
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { createRunnerBridge } = require('./bridge.cjs')
const { sleep } = require('../shared/utils.cjs')

function makeFakeProcess() {
  const emitter = new EventEmitter()
  let started = false
  const calls = { startArgs: null }

  const factory = () => ({
    start: async args => {
      calls.startArgs = args
      started = true
      emitter.emit('event', { type: 'start', pid: 9999, command: 'fake', args: [] })
      return { running: true, pid: 9999 }
    },
    stop: async () => {
      if (!started) return { ok: true, noop: true }
      started = false
      emitter.emit('event', { type: 'exit', code: 0, signal: null })
      return { ok: true, code: 0, signal: null }
    },
    getStatus: () => ({ running: started, pid: started ? 9999 : null }),
    onEvent: cb => {
      emitter.on('event', cb)
      return () => emitter.off('event', cb)
    },
    waitForReady: async () => {
      await sleep(20)
      return { running: true, pid: 9999 }
    },
    signalReady: () => emitter.emit('event', { type: 'ready' })
  })
  return { emitter, factory, calls }
}

function makeFakeWsServer() {
  const emitter = new EventEmitter()
  let connected = false
  let lastAuthToken = null

  const factory = ({ authToken } = {}) => ({
    start: async ({ path: ipcPath } = {}) => {
      connected = true
      lastAuthToken = authToken
      emitter.lastPath = ipcPath
      setTimeout(() => {
        emitter.emit('event', { type: 'connected' })
        emitter.emit('event', { type: 'runner_ready' })
      }, 10)
      return { transport: process.platform === 'win32' ? 'pipe' : 'unix', path: ipcPath }
    },
    stop: async () => {
      connected = false
      return { ok: true }
    },
    call: async (method, params) => {
      if (method === 'get_tools') {
        return { tools: [{ name: 'test_tool', description: 'A test tool' }] }
      }
      return { ok: true, method, params }
    },
    onEvent: cb => {
      emitter.on('event', cb)
      return () => emitter.off('event', cb)
    },
    getStatus: () => ({
      connected,
      transport: connected ? 'pipe' : null,
      path: connected ? emitter.lastPath : null,
      pendingCalls: 0
    })
  })
  return { emitter, factory, getLastAuthToken: () => lastAuthToken }
}

function makeFakeReverseRpc() {
  const factory = () => async method => {
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
    processFactory: process.factory,
    wsServerFactory: wsServer.factory,
    reverseRpcFactory: reverseRpc.factory,
    log: () => {},
    ...overrides
  })
  return { process, wsServer, reverseRpc, bridge }
}

async function waitForPhase(bridge, phase, timeoutMs = 2000) {
  if (bridge.getStatus().phase === phase) return
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`Timeout waiting for phase '${phase}'`)), timeoutMs)
    const unsub = bridge.onEvent(ev => {
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
  assert.equal(status.wsServer.connected, true)
  assert.equal(status.runner.pid, 9999)
  assert.match(
    status.wsServer.path,
    process.platform === 'win32' ? /\\\\\.\\pipe\\deskagent-runner-\d+/ : /runner-\d+\.sock$/
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
    process.platform === 'win32' ? /\\\\\.\\pipe\\deskagent-runner-\d+/ : /runner-\d+\.sock$/
  )
  await bridge.stop()
})

test('start() writes the endpoint file with the IPC schema when deskagentHome is set', async () => {
  const deskagentHome = fs.mkdtempSync(path.join(os.tmpdir(), 'deskagent-bridge-test-'))
  const { bridge, wsServer } = makeBridge({ deskagentHome })

  bridge.start({ readyTimeoutMs: 2_000 })
  await waitForPhase(bridge, 'running')

  const endpointFile = path.join(deskagentHome, 'desktop-endpoint.json')
  const payload = JSON.parse(fs.readFileSync(endpointFile, 'utf8'))
  assert.equal(payload.transport, process.platform === 'win32' ? 'pipe' : 'unix')
  assert.equal(payload.path, wsServer.emitter.lastPath)
  assert.equal(payload.pid, process.pid)
  assert.equal(payload.token, wsServer.getLastAuthToken())
  assert.equal(typeof payload.timestamp, 'number')

  await bridge.stop()
  assert.equal(fs.existsSync(endpointFile), false, 'stop() must remove the endpoint file')
  fs.rmSync(deskagentHome, { recursive: true, force: true })
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
  const events = []
  bridge.onEvent(ev => events.push(ev.type))

  bridge.start({ readyTimeoutMs: 2_000 })
  await waitForPhase(bridge, 'running')

  assert.ok(events.includes('running'))
  await bridge.stop()
})

test('swallows the runner-rpc-ws `connected` lifecycle event', async () => {
  // ``connected`` is emitted on every handshake (initial + reconnect) and must
  // not be logged as an unhandled event; only ``notification`` should surface.
  const logs = []
  const { bridge, wsServer } = makeBridge({ log: msg => logs.push(msg) })

  bridge.start({ readyTimeoutMs: 2_000 })
  await waitForPhase(bridge, 'running')

  // Replay the lifecycle from a fresh handshake (simulates reconnect).
  wsServer.emitter.emit('event', { type: 'connected' })
  wsServer.emitter.emit('event', { type: 'notification', method: 'future.foo' })

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

  const result = await bridge.invoke('test_tool', { x: 1 }, { id: 'c1' })
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
    processFactory: failingFactory,
    wsServerFactory: wsServer.factory,
    log: () => {}
  })

  await assert.rejects(bridge.start({ readyTimeoutMs: 2_000 }), err => /not ready/.test(err.message))
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
  await assert.rejects(bridge.start({ readyTimeoutMs: 2_000 }), err => /already running/.test(err.message))
  await bridge.stop()
})

test('invoke() throws when runner is not connected', async () => {
  const { bridge } = makeBridge()
  await assert.rejects(bridge.invoke('test_tool', {}), err => /not connected/.test(err.message))
})
