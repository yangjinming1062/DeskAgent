import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

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

  /** 创建带 mock socket 的客户端，推进计时器使 connect() 完成。 */
  async function connectClient() {
    let mockSocket!: MockWebSocket

    const client = new JsonRpcGatewayClient({
      socketFactory: () => {
        mockSocket = new MockWebSocket()

        return mockSocket as unknown as WebSocket
      }
    })

    const connectPromise = client.connect('ws://localhost:8000')
    await vi.advanceTimersByTimeAsync(0)
    await connectPromise

    return { client, socket: mockSocket }
  }

  it('updates lastReceivedSeq, drops duplicate frames, and schedules ACK on duplicate', async () => {
    const { client, socket: mockSocket } = await connectClient()

    const received: string[] = []
    client.on('message.delta', ev => {
      received.push((ev.payload as { text: string }).text)
    })

    mockSocket.emitMessage(
      JSON.stringify({
        jsonrpc: '2.0',
        method: 'event',
        params: { type: 'message.delta', seq: 1, payload: { text: 'chunk1' } }
      })
    )
    expect(client.lastReceivedSeq).toBe(1)
    expect(received).toEqual(['chunk1'])

    // 推进定时器以 flush 第一次调度的 ACK
    await vi.advanceTimersByTimeAsync(1000)
    expect(mockSocket.sent.some(msg => msg.includes('session.ack') && msg.includes('"seq":1'))).toBe(true)
    mockSocket.sent.length = 0

    // 重复 seq=1 帧
    mockSocket.emitMessage(
      JSON.stringify({
        jsonrpc: '2.0',
        method: 'event',
        params: { type: 'message.delta', seq: 1, payload: { text: 'chunk1' } }
      })
    )
    // 不应重复派发
    expect(client.lastReceivedSeq).toBe(1)
    expect(received).toEqual(['chunk1'])

    // 应仍为重复帧调度 ACK
    await vi.advanceTimersByTimeAsync(1000)
    expect(mockSocket.sent.some(msg => msg.includes('session.ack') && msg.includes('"seq":1'))).toBe(true)

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

  it('safely drops invalid schema frames without throwing', async () => {
    let mockSocket: MockWebSocket | null = null

    const client = new JsonRpcGatewayClient({
      socketFactory: () => {
        mockSocket = new MockWebSocket()

        return mockSocket as unknown as WebSocket
      }
    })

    const connectPromise = client.connect('ws://localhost:9999')
    vi.runAllTimers()
    await connectPromise

    expect(() => {
      mockSocket?.emitMessage('not json at all')
      mockSocket?.emitMessage(JSON.stringify({ notAValidRpcFrame: 123 }))
    }).not.toThrow()
  })

  it('drops event frame with non-string params.type', async () => {
    const { client, socket: mockSocket } = await connectClient()

    let dispatched = false
    client.on('message.delta', () => {
      dispatched = true
    })

    mockSocket.emitMessage(JSON.stringify({ method: 'event', params: { type: 42, seq: 1 } }))

    expect(dispatched).toBe(false)
    expect(client.lastReceivedSeq).toBe(0)
  })

  it('dispatches event when id is null alongside event shape', async () => {
    const { client, socket: mockSocket } = await connectClient()

    let captured: unknown = null
    client.on('foo', ev => {
      captured = ev
    })

    mockSocket.emitMessage(JSON.stringify({ id: null, method: 'event', params: { type: 'foo', seq: 1, tag: 'a' } }))

    expect((captured as { tag: string } | null)?.tag).toBe('a')
    expect(client.lastReceivedSeq).toBe(1)
  })

  it('drops frame with top-level seq that is not a number', async () => {
    const { client, socket: mockSocket } = await connectClient()

    let dispatched = false
    client.on('message.delta', () => {
      dispatched = true
    })

    mockSocket.emitMessage(JSON.stringify({ seq: '1', method: 'event', params: { type: 'message.delta', seq: 1 } }))

    expect(dispatched).toBe(false)
    expect(client.lastReceivedSeq).toBe(0)
  })

  it('preserves passthrough fields on params (event handler sees extras)', async () => {
    const { client, socket: mockSocket } = await connectClient()

    let captured: unknown = null
    client.on('x', ev => {
      captured = ev
    })

    mockSocket.emitMessage(JSON.stringify({ method: 'event', params: { type: 'x', seq: 1, custom: 'y' } }))

    expect((captured as Record<string, unknown> | null)?.custom).toBe('y')
  })

  it('falls back to InternalError when response error.code is absent', async () => {
    const { client, socket: mockSocket } = await connectClient()

    const pending = client.request('test.method')

    mockSocket.emitMessage(JSON.stringify({ id: 'r1', error: { message: 'failed' } }))

    await expect(pending).rejects.toThrow(SpiritAgentRpcError)
    await expect(pending).rejects.toMatchObject({ code: SpiritAgentRpcErrorCode.InternalError, message: 'failed' })
  })
})
