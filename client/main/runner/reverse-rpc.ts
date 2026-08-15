export const DEFAULT_TIMEOUT_MS = 120_000

// JSON-RPC 2.0 §5.1 — "Method not found".
export const METHOD_NOT_FOUND_CODE = -32601

export interface ReverseRpcOptions {
  backendSession?: any
  log?: (chunk: string) => void
}

export function createReverseRpc(options: ReverseRpcOptions = {}): (method: string, params: any) => Promise<any> {
  const log = typeof options.log === 'function' ? options.log : () => {}
  const backendSession = options.backendSession

  if (!backendSession) {
    throw new TypeError('createReverseRpc requires options.backendSession.')
  }

  // Per-session cumulative limits; the budget resets when the runner reconnects (fresh WS session).
  const MAX_MESSAGES_PER_SESSION = 200
  const MAX_TEXT_BYTES_PER_SESSION = 1 * 1024 * 1024
  const MAX_VISION_BYTES_PER_SESSION = 10 * 1024 * 1024
  let sessionMessagesSent = 0
  let sessionBytesSent = 0

  function _isVisionRequest(messages: any[]): boolean {
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

  async function handleRequestLlm(params: any): Promise<any> {
    const session = backendSession.getSession()

    if (!session?.hasToken) {
      throw new Error('No active session — cannot proxy LLM request.')
    }

    const messages = params.messages || []
    const messageCount = messages.length

    if (messageCount > MAX_MESSAGES_PER_SESSION) {
      throw new Error(
        `request_llm rejected: too many messages in this call (${messageCount} > ${MAX_MESSAGES_PER_SESSION}).`
      )
    }

    const payloadBytes = Buffer.byteLength(JSON.stringify(messages), 'utf8')
    const isVision = _isVisionRequest(messages)
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

    const token = typeof backendSession.getToken === 'function' ? backendSession.getToken() : session.token

    const client =
      typeof backendSession.client === 'function'
        ? backendSession.client()
        : backendSession.clientInstance || backendSession

    return client.post('/api/llm/completion', {
      body: {
        max_tokens: params.max_tokens || undefined,
        messages,
        model: params.model || undefined,
        temperature: params.temperature || undefined
      },
      timeoutMs: DEFAULT_TIMEOUT_MS,
      token
    })
  }

  const handlers: Record<string, (params: any) => Promise<any>> = {
    request_llm: handleRequestLlm
  }

  async function handleRequest(method: string, params: any): Promise<any> {
    const handler = handlers[method]

    if (!handler) {
      const error: any = new Error(`Unknown reverse RPC method: ${method}`)
      error.code = METHOD_NOT_FOUND_CODE
      throw error
    }

    return handler(params)
  }

  return handleRequest
}
