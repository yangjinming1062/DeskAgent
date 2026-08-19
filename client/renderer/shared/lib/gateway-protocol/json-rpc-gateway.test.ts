import { describe, expect, it } from 'vitest'

import { JsonRpcGatewayClient, SpiritAgentRpcError, SpiritAgentRpcErrorCode } from './json-rpc-gateway'

describe('SpiritAgentRpcError', () => {
  it('exposes the JSON-RPC 2.0 standard codes', () => {
    expect(SpiritAgentRpcErrorCode.ParseError).toBe(-32700)
    expect(SpiritAgentRpcErrorCode.InvalidRequest).toBe(-32600)
    expect(SpiritAgentRpcErrorCode.MethodNotFound).toBe(-32601)
    expect(SpiritAgentRpcErrorCode.InvalidParams).toBe(-32602)
    expect(SpiritAgentRpcErrorCode.InternalError).toBe(-32603)
  })

  it('extends Error so existing catch blocks still match', () => {
    const err = new SpiritAgentRpcError(-32603, 'Tool execution timeout')
    expect(err).toBeInstanceOf(Error)
    expect(err).toBeInstanceOf(SpiritAgentRpcError)
    expect(err.name).toBe('SpiritAgentRpcError')
  })

  it('preserves code, message and optional data', () => {
    const err = new SpiritAgentRpcError(-32602, 'session_id missing', { field: 'session_id' })
    expect(err.code).toBe(-32602)
    expect(err.message).toBe('session_id missing')
    expect(err.data).toEqual({ field: 'session_id' })
  })
})

describe('JsonRpcGatewayClient Sequence Tracking & Deduplication', () => {
  class MockWebSocket {
    static OPEN = 1
    readyState = MockWebSocket.OPEN
    private listeners: Record<string, ((ev: unknown) => void)[]> = {}
    sent: string[] = []

    constructor() {
      setTimeout(() => {
        for (const fn of this.listeners['open'] ?? []) {
          fn({})
        }
      }, 0)
    }

    addEventListener(type: string, fn: (ev: unknown) => void) {
      ;(this.listeners[type] ??= []).push(fn)
    }

    removeEventListener(type: string, fn: (ev: unknown) => void) {
      this.listeners[type] = (this.listeners[type] ?? []).filter(cb => cb !== fn)
    }

    send(data: string) {
      this.sent.push(data)
    }

    close() {
      this.readyState = 3
    }

    emitMessage(data: string) {
      for (const fn of this.listeners['message'] ?? []) {
        fn({ data })
      }
    }
  }

  it('updates lastReceivedSeq and drops duplicate frames', async () => {
    let mockSocket!: MockWebSocket

    const client = new JsonRpcGatewayClient({
      socketFactory: () => {
        mockSocket = new MockWebSocket()

        return mockSocket as unknown as WebSocket
      }
    })

    const received: string[] = []
    client.on('message.delta', ev => {
      received.push((ev.payload as { text: string }).text)
    })

    await client.connect('ws://localhost:8000')

    // 接收 seq=1 的帧
    mockSocket.emitMessage(
      JSON.stringify({
        jsonrpc: '2.0',
        method: 'event',
        params: { type: 'message.delta', seq: 1, payload: { text: 'chunk1' } }
      })
    )
    expect(client.lastReceivedSeq).toBe(1)
    expect(received).toEqual(['chunk1'])

    // 接收重复的 seq=1 帧——应被丢弃
    mockSocket.emitMessage(
      JSON.stringify({
        jsonrpc: '2.0',
        method: 'event',
        params: { type: 'message.delta', seq: 1, payload: { text: 'chunk1' } }
      })
    )
    expect(client.lastReceivedSeq).toBe(1)
    expect(received).toEqual(['chunk1'])

    // 接收 seq=2 的帧
    mockSocket.emitMessage(
      JSON.stringify({
        jsonrpc: '2.0',
        method: 'event',
        params: { type: 'message.delta', seq: 2, payload: { text: 'chunk2' } }
      })
    )
    expect(client.lastReceivedSeq).toBe(2)
    expect(received).toEqual(['chunk1', 'chunk2'])
  })

  it('resets sequence counter', () => {
    const client = new JsonRpcGatewayClient()
    client.resetSeq(0)
    expect(client.lastReceivedSeq).toBe(0)
    client.resetSeq(42)
    expect(client.lastReceivedSeq).toBe(42)
  })
})
