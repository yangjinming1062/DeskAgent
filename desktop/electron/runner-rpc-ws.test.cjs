/**
 * Tests for electron/runner-rpc-ws.cjs.
 *
 * Run with: node --test electron/runner-rpc-ws.test.cjs
 */

const test = require('node:test')
const assert = require('node:assert/strict')
const { createRunnerWsServer } = require('./runner-rpc-ws.cjs')

function makeWsServer(overrides = {}) {
  return createRunnerWsServer({
    log: () => {},
    ...overrides
  })
}

test('start() binds to a port and returns it', async () => {
  const server = makeWsServer()
  const { port } = await server.start()
  assert.ok(port > 0)
  await server.stop()
})

test('getStatus() reflects initial state', async () => {
  const server = makeWsServer()
  const status = server.getStatus()
  assert.equal(status.connected, false)
  assert.equal(status.port, 0)
  assert.equal(status.pendingCalls, 0)
})

test('stop() returns ok when not started', async () => {
  const server = makeWsServer()
  const result = await server.stop()
  assert.equal(result.ok, true)
})

test('call() rejects when server is closed', async () => {
  const server = makeWsServer()
  await server.stop()
  await assert.rejects(server.call('test', {}), err => /closed/.test(err.message))
})

test('onEvent() returns unsubscribe function', async () => {
  const server = makeWsServer()
  const events = []
  const unsub = server.onEvent(ev => events.push(ev))
  unsub()
  assert.ok(typeof unsub === 'function')
  await server.stop()
})

test('start() can be called twice (idempotent)', async () => {
  const server = makeWsServer()
  const { port: p1 } = await server.start()
  const { port: p2 } = await server.start()
  assert.equal(p1, p2)
  await server.stop()
})
