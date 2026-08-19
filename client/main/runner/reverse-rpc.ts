import type { BackendClient } from '../backend/client'

export const DEFAULT_TIMEOUT_MS = 120_000

// JSON-RPC 2.0 §5.1 — "Method not found".
export const METHOD_NOT_FOUND_CODE = -32601

export interface BackendSessionLike {
  client?: () => BackendClient
  clientInstance?: BackendClient
  getSession: () => null | { hasToken: boolean; token?: null | string }
  getToken?: () => null | string
}

export interface ReverseRpcOptions {
  backendSession?: BackendSessionLike | null
  log?: (chunk: string) => void
}

export interface LlmMessageContentPart {
  image?: unknown
  image_url?: unknown
  type?: string
}

export interface LlmMessage {
  content?: string | LlmMessageContentPart[]
  role?: string
}

export interface LlmCompletionParams {
  max_tokens?: number
  messages?: LlmMessage[]
  model?: string
  temperature?: number
}

export function createReverseRpc(
  options: ReverseRpcOptions = {}
): (method: string, params?: unknown) => Promise<unknown> {
  const log = typeof options.log === 'function' ? options.log : () => {}
  const backendSession = options.backendSession

  if (!backendSession) {
    throw new TypeError('createReverseRpc requires options.backendSession.')
  }

  // 单会话累计限额；Runner 重新连接时（新的 WS 会话）配额会重置。
  const MAX_MESSAGES_PER_SESSION = 200
  const MAX_TEXT_BYTES_PER_SESSION = 1 * 1024 * 1024
  const MAX_VISION_BYTES_PER_SESSION = 10 * 1024 * 1024
  let sessionMessagesSent = 0
  let sessionBytesSent = 0

  function isVisionRequest(messages: LlmMessage[]): boolean {
    if (!Array.isArray(messages)) {
      return false
    }

    for (const msg of messages) {
      if (Array.isArray(msg?.content)) {
        for (const part of msg.content) {
          if (part?.type === 'image_url' || part?.type === 'image' || part?.image_url) {
            return true
          }
        }
      }
    }

    return false
  }

  async function handleRequestLlm(params: unknown): Promise<unknown> {
    const session = backendSession!.getSession()

    if (!session?.hasToken) {
      throw new Error('No active session — cannot proxy LLM request.')
    }

    const payloadObj = (params as LlmCompletionParams) || {}
    const messages = payloadObj.messages || []
    const messageCount = messages.length

    if (messageCount > MAX_MESSAGES_PER_SESSION) {
      throw new Error(
        `request_llm rejected: too many messages in this call (${messageCount} > ${MAX_MESSAGES_PER_SESSION}).`
      )
    }

    const payloadBytes = Buffer.byteLength(JSON.stringify(messages), 'utf8')
    const isVision = isVisionRequest(messages)
    const maxRequestBytes = isVision ? MAX_VISION_BYTES_PER_SESSION : MAX_TEXT_BYTES_PER_SESSION
    const maxSessionBytes = isVision ? MAX_VISION_BYTES_PER_SESSION : MAX_TEXT_BYTES_PER_SESSION

    if (payloadBytes > maxRequestBytes) {
      throw new Error(`request_llm rejected: messages payload too large (${payloadBytes} bytes > ${maxRequestBytes}).`)
    }

    sessionMessagesSent += messageCount

    if (sessionMessagesSent > MAX_MESSAGES_PER_SESSION) {
      throw new Error(
        `request_llm rejected: session exceeded ${MAX_MESSAGES_PER_SESSION} messages (sent ${sessionMessagesSent}).`
      )
    }

    sessionBytesSent += payloadBytes

    if (sessionBytesSent > maxSessionBytes) {
      throw new Error(`request_llm rejected: session exceeded ${maxSessionBytes} bytes (sent ${sessionBytesSent}).`)
    }

    log(
      `[reverse-rpc] request_llm (${messageCount} messages, ${payloadBytes} bytes, session ${sessionMessagesSent}/${sessionBytesSent})`
    )

    const token =
      typeof backendSession!.getToken === 'function' ? backendSession!.getToken() : (session.token ?? undefined)

    const client =
      typeof backendSession!.client === 'function'
        ? backendSession!.client()
        : (backendSession!.clientInstance as BackendClient)

    return client.post('/api/llm/completion', {
      body: {
        max_tokens: payloadObj.max_tokens || undefined,
        messages,
        model: payloadObj.model || undefined,
        temperature: payloadObj.temperature || undefined
      },
      timeoutMs: DEFAULT_TIMEOUT_MS,
      token: token || undefined
    })
  }

  const handlers: Record<string, (params: unknown) => Promise<unknown>> = {
    request_llm: handleRequestLlm
  }

  async function handleRequest(method: string, params?: unknown): Promise<unknown> {
    const handler = handlers[method]

    if (!handler) {
      const error = new Error(`Unknown reverse RPC method: ${method}`) as Error & { code?: number }
      error.code = METHOD_NOT_FOUND_CODE
      throw error
    }

    return handler(params)
  }

  return handleRequest
}
