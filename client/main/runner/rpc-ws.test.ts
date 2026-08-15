import assert from 'node:assert/strict'
import { once } from 'node:events'
import fs from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import WebSocket from 'ws'

import { createRunnerWsServer } from './rpc-ws'

const AUTH_TOKEN = 'a'.repeat(64)

function makeWsServer(overrides = {}) {
  return createRunnerWsServer({
    authToken: AUTH_TOKEN,
    log: () => {},
    ...overrides
  })
}

function makeIpcPath(): string {
  if (process.platform === 'win32') {
    return `\\\\.\\pipe\\deskagent-test-${process.pid}-${Date.now()}`
  }

  return path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'deskagent-ws-test-')), 'runner.sock')
}

function wsConnect(ipcPath: string, extraHeaders: Record<string, string> = {}): WebSocket {
  return new WebSocket('ws://deskagent/rpc', {
    createConnection: () => net.connect(ipcPath),
    headers: { 'x-deskagent-auth': AUTH_TOKEN, ...extraHeaders }
  })
}

test('start() binds to the IPC path and returns transport + path', async () => {
  const server = makeWsServer()
  const ipcPath = makeIpcPath()
  const started = await server.start({ path: ipcPath })
  assert.equal(started.transport, process.platform === 'win32' ? 'pipe' : 'unix')
  assert.equal(started.path, ipcPath)

  if (process.platform !== 'win32') {
    const mode = fs.statSync(ipcPath).mode & 0o777
    assert.equal(mode, 0o600, `socket mode should be 0600, got ${mode.toString(8)}`)
  }

  await server.stop()
})

test('getStatus() reflects initial state', async () => {
  const server = makeWsServer()
  const status = server.getStatus()
  assert.equal(status.connected, false)
  assert.equal(status.transport, null)
  assert.equal(status.path, null)
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
  await assert.rejects(server.call('test', {}), (err: any) => /closed/.test(err.message))
})

test('onEvent() returns unsubscribe function', async () => {
  const server = makeWsServer()
  const events: any[] = []
  const unsub = server.onEvent(ev => events.push(ev))
  unsub()
  assert.ok(typeof unsub === 'function')
  await server.stop()
})

test('start() can be called twice (idempotent)', async () => {
  const server = makeWsServer()
  const ipcPath = makeIpcPath()
  const first = await server.start({ path: ipcPath })
  const second = await server.start()
  assert.equal(second.path, first.path)
  await server.stop()
})

test('start() requires an authToken', async () => {
  const server = createRunnerWsServer({ log: () => {} })
  await assert.rejects(server.start({ path: makeIpcPath() }), (err: any) => /authToken/.test(err.message))
})

test('authenticated client completes the handshake and delivers notifications', async () => {
  const server = makeWsServer()
  const ipcPath = makeIpcPath()
  await server.start({ path: ipcPath })

  const events: any[] = []
  server.onEvent(ev => events.push(ev))
  const client = wsConnect(ipcPath)
  await once(client, 'open')
  client.send(JSON.stringify({ jsonrpc: '2.0', method: 'runner_ready', params: { version: 'test' } }))

  await new Promise<void>(resolve => {
    const timer = setInterval(() => {
      if (events.some(ev => ev.type === 'runner_ready')) {
        clearInterval(timer)
        resolve()
      }
    }, 20)
  })
  assert.equal(server.getStatus().connected, true)
  client.close()
  await once(client, 'close')
  await server.stop()
})

test('a second authenticated connection replaces the first with code 1000', async () => {
  const server = makeWsServer()
  const ipcPath = makeIpcPath()
  await server.start({ path: ipcPath })

  const first = wsConnect(ipcPath)
  await once(first, 'open')
  const second = wsConnect(ipcPath)
  await once(second, 'open')

  const [closeEvent] = await once(first, 'close')
  assert.equal(closeEvent, 1000, 'existing connection should be replaced with 1000')
  second.close()
  await once(second, 'close')
  await server.stop()
})

test('a bad handshake token gets HTTP 401 and never opens; existing connections survive', async () => {
  const server = makeWsServer()
  const ipcPath = makeIpcPath()
  await server.start({ path: ipcPath })

  const good = wsConnect(ipcPath)
  await once(good, 'open')

  const bad = wsConnect(ipcPath, { 'x-deskagent-auth': '0'.repeat(64) })
  const [error] = await once(bad, 'error')
  assert.match(String((error as any).message), /401|Unexpected server response/)

  bad.terminate()
  await Promise.race([once(bad, 'close'), new Promise(resolve => setTimeout(resolve, 500))])

  assert.equal(good.readyState, 1, 'authenticated connection must survive a rejected upgrade')
  assert.equal(server.getStatus().connected, true)

  good.close()
  await once(good, 'close')
  await server.stop()
})
