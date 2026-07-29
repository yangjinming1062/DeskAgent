import { describe, expect, it } from 'vitest'

import { DeskAgentRpcError, DeskAgentRpcErrorCode } from './json-rpc-gateway'

describe('DeskAgentRpcError', () => {
  it('exposes the JSON-RPC 2.0 standard codes', () => {
    expect(DeskAgentRpcErrorCode.ParseError).toBe(-32700)
    expect(DeskAgentRpcErrorCode.InvalidRequest).toBe(-32600)
    expect(DeskAgentRpcErrorCode.MethodNotFound).toBe(-32601)
    expect(DeskAgentRpcErrorCode.InvalidParams).toBe(-32602)
    expect(DeskAgentRpcErrorCode.InternalError).toBe(-32603)
  })

  it('extends Error so existing catch blocks still match', () => {
    const err = new DeskAgentRpcError(-32603, 'Tool execution timeout')
    expect(err).toBeInstanceOf(Error)
    expect(err).toBeInstanceOf(DeskAgentRpcError)
    expect(err.name).toBe('DeskAgentRpcError')
  })

  it('preserves code, message and optional data', () => {
    const err = new DeskAgentRpcError(-32602, 'session_id missing', { field: 'session_id' })
    expect(err.code).toBe(-32602)
    expect(err.message).toBe('session_id missing')
    expect(err.data).toEqual({ field: 'session_id' })
  })
})
