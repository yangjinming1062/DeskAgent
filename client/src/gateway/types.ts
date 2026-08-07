export type GatewayState = 'idle' | 'connecting' | 'open' | 'closed' | 'error'

export interface RpcEvent {
  type: string
  payload?: unknown
  session_id?: string
}

export interface RpcRequest {
  jsonrpc: '2.0'
  id: number
  method: string
  params: Record<string, unknown>
}

export interface RpcResponse {
  jsonrpc: '2.0'
  id: number
  result?: unknown
  error?: { code: number; message: string; data?: unknown }
}

export interface RpcNotification {
  jsonrpc: '2.0'
  method: 'event'
  params: RpcEvent
}

export type RpcMessage = RpcRequest | RpcResponse | RpcNotification
