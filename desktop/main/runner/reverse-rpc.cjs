/**
 * Handles reverse RPC requests from Runner — currently `request_llm`,
 * which proxies an LLM completion through Backend's POST /api/llm/completion.
 *
 * Pure: no electron require. Call sites inject `backendSession`.
 */

const DEFAULT_TIMEOUT_MS = 120_000
// Guard against misbehaving Runner pushing arbitrarily large payloads.
const MAX_REQUEST_PAYLOAD_BYTES = 1 * 1024 * 1024

// JSON-RPC 2.0 §5.1 — "Method not found".
const METHOD_NOT_FOUND_CODE = -32601

function createReverseRpc(options = {}) {
  const log = typeof options.log === 'function' ? options.log : () => {}
  const backendSession = options.backendSession

  if (!backendSession) {
    throw new TypeError('createReverseRpc requires options.backendSession.')
  }

  // Per-session cumulative limits; the budget resets when the runner reconnects (fresh WS session).
  const MAX_MESSAGES_PER_SESSION = 200
  const MAX_BYTES_PER_SESSION = 1 * 1024 * 1024
  let sessionMessagesSent = 0
  let sessionBytesSent = 0

  async function handleRequestLlm(params) {
    const session = backendSession.getSession()
    if (!session?.hasToken) {
      throw new Error('No active session — cannot proxy LLM request.')
    }

    const messages = params.messages || []
    const messageCount = messages.length

    if (messageCount > MAX_MESSAGES_PER_SESSION) {
      throw new Error(`request_llm rejected: too many messages in this call (${messageCount} > ${MAX_MESSAGES_PER_SESSION}).`)
    }

    const payloadBytes = Buffer.byteLength(JSON.stringify(messages), 'utf8')

    if (payloadBytes > MAX_REQUEST_PAYLOAD_BYTES) {
      throw new Error(
        `request_llm rejected: messages payload too large (${payloadBytes} bytes > ${MAX_REQUEST_PAYLOAD_BYTES}).`
      )
    }

    sessionMessagesSent += messageCount
    if (sessionMessagesSent > MAX_MESSAGES_PER_SESSION) {
      throw new Error(
        `request_llm rejected: session exceeded ${MAX_MESSAGES_PER_SESSION} messages (sent ${sessionMessagesSent}).`
      )
    }
    sessionBytesSent += payloadBytes
    if (sessionBytesSent > MAX_BYTES_PER_SESSION) {
      throw new Error(
        `request_llm rejected: session exceeded ${MAX_BYTES_PER_SESSION} bytes (sent ${sessionBytesSent}).`
      )
    }

    log(`[reverse-rpc] request_llm (${messageCount} messages, ${payloadBytes} bytes, session ${sessionMessagesSent}/${sessionBytesSent})`)

    return backendSession.client().post('/api/llm/completion', {
      body: {
        messages,
        model: params.model || undefined,
        temperature: params.temperature || undefined,
        max_tokens: params.max_tokens || undefined
      },
      token: session.token,
      timeoutMs: DEFAULT_TIMEOUT_MS
    })
  }

  const handlers = {
    request_llm: handleRequestLlm
  }

  async function handleRequest(method, params) {
    const handler = handlers[method]
    if (!handler) {
      const error = new Error(`Unknown reverse RPC method: ${method}`)
      error.code = METHOD_NOT_FOUND_CODE
      throw error
    }
    return handler(params)
  }

  return handleRequest
}

module.exports = {
  createReverseRpc,
  DEFAULT_TIMEOUT_MS
}
