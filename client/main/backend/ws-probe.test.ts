import assert from 'node:assert/strict'
import test from 'node:test'

import { probeGatewayWebSocket, type WebSocketConstructor, type WebSocketLike } from './ws-probe'

function makeFakeWs() {
  const instances: FakeWs[] = []

  class FakeWs implements WebSocketLike {
    closed: boolean
    listeners: Record<string, ((...args: unknown[]) => void)[]>
    url: string

    constructor(url: string) {
      this.url = url
      this.listeners = {}
      this.closed = false
      instances.push(this)
    }
    addEventListener(type: string, fn: (...args: unknown[]) => void) {
      ;(this.listeners[type] ||= []).push(fn)
    }
    close() {
      this.closed = true
    }
    emit(type: string, event?: unknown) {
      for (const fn of this.listeners[type] || []) {
        fn(event)
      }
    }
  }

  return { FakeWs, instances }
}

const FAST = { connectTimeoutMs: 1_000, readyGraceMs: 10 }

test('probe resolves ok when the socket opens and stays open', async () => {
  const { FakeWs, instances } = makeFakeWs()
  const promise = probeGatewayWebSocket('ws://host/api/ws?token=t', { WebSocketImpl: FakeWs, ...FAST })
  instances[0].emit('open')
  const result = await promise
  assert.deepEqual(result, { ok: true })
  assert.equal(instances[0].closed, true)
})

test('probe resolves ok immediately when a frame arrives', async () => {
  const { FakeWs, instances } = makeFakeWs()

  const promise = probeGatewayWebSocket('ws://host/api/ws?token=t', {
    connectTimeoutMs: 1_000,
    readyGraceMs: 10_000,
    WebSocketImpl: FakeWs
  })

  instances[0].emit('open')
  instances[0].emit('message', { data: '{"jsonrpc":"2.0"}' })
  const result = await promise
  assert.deepEqual(result, { ok: true })
})

test('probe fails when the socket errors before opening', async () => {
  const { FakeWs, instances } = makeFakeWs()
  const promise = probeGatewayWebSocket('ws://host/api/ws?token=t', { WebSocketImpl: FakeWs, ...FAST })
  instances[0].emit('error', { message: 'ECONNREFUSED' })
  const result = await promise
  assert.equal(result.ok, false)
  assert.match(result.reason!, /ECONNREFUSED/)
})

test('probe fails when the gateway closes before opening', async () => {
  const { FakeWs, instances } = makeFakeWs()
  const promise = probeGatewayWebSocket('ws://host/api/ws?token=t', { WebSocketImpl: FakeWs, ...FAST })
  instances[0].emit('close', { code: 1006 })
  const result = await promise
  assert.equal(result.ok, false)
  assert.match(result.reason!, /before it opened/)
  assert.match(result.reason!, /1006/)
})

test('probe fails when the gateway accepts then immediately closes (auth rejected)', async () => {
  const { FakeWs, instances } = makeFakeWs()
  const promise = probeGatewayWebSocket('ws://host/api/ws?token=t', { WebSocketImpl: FakeWs, ...FAST })
  instances[0].emit('open')
  instances[0].emit('close', { code: 4403, reason: 'forbidden' })
  const result = await promise
  assert.equal(result.ok, false)
  assert.match(result.reason!, /credential rejected/)
  assert.match(result.reason!, /4403/)
  assert.match(result.reason!, /forbidden/)
})

test('probe times out when the socket never opens', async () => {
  const { FakeWs } = makeFakeWs()

  const result = await probeGatewayWebSocket('ws://host/api/ws?token=t', {
    connectTimeoutMs: 20,
    readyGraceMs: 10,
    WebSocketImpl: FakeWs
  })

  assert.equal(result.ok, false)
  assert.match(result.reason!, /Timed out after 20ms/)
})

test('probe fails gracefully when the constructor throws', async () => {
  class ExplodingWs {
    constructor() {
      throw new Error('boom')
    }
  }

  const result = await probeGatewayWebSocket('ws://host/api/ws?token=t', {
    WebSocketImpl: ExplodingWs as unknown as WebSocketConstructor,
    ...FAST
  })

  assert.equal(result.ok, false)
  assert.match(result.reason!, /boom/)
})

test('probe reports unavailable when no WebSocket implementation is provided', async () => {
  const result = await probeGatewayWebSocket('ws://host/api/ws?token=t', {
    WebSocketImpl: undefined,
    ...FAST
  })

  assert.equal(result.ok, false)
  assert.match(result.reason!, /not available/)
})
