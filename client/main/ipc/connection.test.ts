import assert from 'node:assert/strict'
import test from 'node:test'

import { type DesktopBootProgress, IPC, type SpiritAgentConnection } from '@ipc/contracts'
import type { IpcMain, IpcMainInvokeEvent } from 'electron'

import { registerConnectionIpc } from './connection'

type IpcHandler = (event: IpcMainInvokeEvent, ...args: unknown[]) => Promise<unknown> | unknown

interface FakeIpc {
  handle: (channel: string, handler: IpcHandler) => void
  invoke: (channel: string, ...args: unknown[]) => Promise<unknown>
}

function makeFakeIpc(): FakeIpc {
  const handlers = new Map<string, IpcHandler>()

  return {
    handle: (channel, handler) => {
      handlers.set(channel, handler)
    },
    invoke: async (channel, ...args) => {
      const h = handlers.get(channel)

      if (!h) {
        throw new Error(`no handler for ${channel}`)
      }

      return h({} as IpcMainInvokeEvent, ...args)
    }
  }
}

test('IPC.invoke.gatewayWsUrl: mintWsTicket 成功时返回新 ticket URL 并更新主进程缓存', async () => {
  const ipc = makeFakeIpc()
  let cachedWsUrl = 'ws://127.0.0.1:8000/api/chat/ws?ticket=OLD_TICKET'

  const fakeConnection: SpiritAgentConnection = {
    baseUrl: 'http://127.0.0.1:8000',
    isFullscreen: false,
    logs: [],
    nativeOverlayWidth: 0,
    token: 'jwt-auth-token-123',
    windowButtonPosition: null,
    wsUrl: cachedWsUrl
  }

  const mintCalls: Array<{ baseUrl: string; token: null | string }> = []

  const mintWsTicket = async (baseUrl: string, token: null | string): Promise<string | null> => {
    mintCalls.push({ baseUrl, token })

    return 'FRESH_TICKET_999'
  }

  registerConnectionIpc({
    ensureBackend: async () => fakeConnection,
    fetchJson: async () => ({}),
    getBootProgressState: () =>
      ({ error: null, message: '', phase: 'idle', progress: 0, running: false }) as DesktopBootProgress,
    ipcMain: ipc as unknown as IpcMain,
    mintWsTicket,
    resolvePathTimeoutMs: () => 15000,
    resolveTimeoutMs: () => 15000,
    setCachedWsUrl: (wsUrl: string) => {
      cachedWsUrl = wsUrl
    }
  })

  const result = await ipc.invoke(IPC.invoke.gatewayWsUrl)
  assert.equal(result, 'ws://127.0.0.1:8000/api/chat/ws?ticket=FRESH_TICKET_999')
  assert.equal(cachedWsUrl, 'ws://127.0.0.1:8000/api/chat/ws?ticket=FRESH_TICKET_999')
  assert.equal(mintCalls.length, 1)
  assert.equal(mintCalls[0].token, 'jwt-auth-token-123')
})

test('IPC.invoke.gatewayWsUrl: mintWsTicket 抛错失败时降级返回 connection.wsUrl', async () => {
  const ipc = makeFakeIpc()
  let cachedWsUrl = 'ws://127.0.0.1:8000/api/chat/ws?ticket=OLD_TICKET'

  const fakeConnection: SpiritAgentConnection = {
    baseUrl: 'http://127.0.0.1:8000',
    isFullscreen: false,
    logs: [],
    nativeOverlayWidth: 0,
    token: 'jwt-auth-token-123',
    windowButtonPosition: null,
    wsUrl: cachedWsUrl
  }

  const mintWsTicket = async (): Promise<string | null> => {
    throw new Error('Network timeout during ticket minting')
  }

  registerConnectionIpc({
    ensureBackend: async () => fakeConnection,
    fetchJson: async () => ({}),
    getBootProgressState: () =>
      ({ error: null, message: '', phase: 'idle', progress: 0, running: false }) as DesktopBootProgress,
    ipcMain: ipc as unknown as IpcMain,
    mintWsTicket,
    resolvePathTimeoutMs: () => 15000,
    resolveTimeoutMs: () => 15000,
    setCachedWsUrl: (wsUrl: string) => {
      cachedWsUrl = wsUrl
    }
  })

  const result = await ipc.invoke(IPC.invoke.gatewayWsUrl)
  assert.equal(result, 'ws://127.0.0.1:8000/api/chat/ws?ticket=OLD_TICKET')
  assert.equal(cachedWsUrl, 'ws://127.0.0.1:8000/api/chat/ws?ticket=OLD_TICKET')
})

test('IPC.invoke.gatewayWsUrl: connection.token 为 null 时跳过 mint 直接返回 connection.wsUrl', async () => {
  const ipc = makeFakeIpc()
  let mintCalled = false

  const fakeConnection: SpiritAgentConnection = {
    baseUrl: 'http://127.0.0.1:8000',
    isFullscreen: false,
    logs: [],
    nativeOverlayWidth: 0,
    token: null,
    windowButtonPosition: null,
    wsUrl: 'ws://127.0.0.1:8000/api/chat/ws'
  }

  const mintWsTicket = async (): Promise<string | null> => {
    mintCalled = true

    return 'TICKET'
  }

  registerConnectionIpc({
    ensureBackend: async () => fakeConnection,
    fetchJson: async () => ({}),
    getBootProgressState: () =>
      ({ error: null, message: '', phase: 'idle', progress: 0, running: false }) as DesktopBootProgress,
    ipcMain: ipc as unknown as IpcMain,
    mintWsTicket,
    resolvePathTimeoutMs: () => 15000,
    resolveTimeoutMs: () => 15000
  })

  const result = await ipc.invoke(IPC.invoke.gatewayWsUrl)
  assert.equal(result, 'ws://127.0.0.1:8000/api/chat/ws')
  assert.equal(mintCalled, false)
})
