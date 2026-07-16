import { describe, expect, it } from 'vitest'

import { ZastRpcError, ZastRpcErrorCode } from './json-rpc-gateway'

describe('ZastRpcError', () => {
  it('exposes the JSON-RPC 2.0 standard codes', () => {
    expect(ZastRpcErrorCode.ParseError).toBe(-32700)
    expect(ZastRpcErrorCode.InvalidRequest).toBe(-32600)
    expect(ZastRpcErrorCode.MethodNotFound).toBe(-32601)
    expect(ZastRpcErrorCode.InvalidParams).toBe(-32602)
    expect(ZastRpcErrorCode.InternalError).toBe(-32603)
  })

  it('extends Error so existing catch blocks still match', () => {
    const err = new ZastRpcError(-32603, 'Tool execution timeout')
    expect(err).toBeInstanceOf(Error)
    expect(err).toBeInstanceOf(ZastRpcError)
    expect(err.name).toBe('ZastRpcError')
  })

  it('preserves code, message and optional data', () => {
    const err = new ZastRpcError(-32602, 'session_id missing', { field: 'session_id' })
    expect(err.code).toBe(-32602)
    expect(err.message).toBe('session_id missing')
    expect(err.data).toEqual({ field: 'session_id' })
  })
})
