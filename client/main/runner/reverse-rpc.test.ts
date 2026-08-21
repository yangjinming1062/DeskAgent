import assert from 'node:assert/strict'
import test from 'node:test'

import type { BackendClient, BackendRequestOptions } from '../backend/client'

import { createReverseRpc } from './reverse-rpc'

function makeFakeSession({ baseUrl = 'https://api.test.com', hasToken = true, token = 'test-token' } = {}) {
  const session = hasToken ? { baseUrl, hasToken: true, token } : { hasToken: false, token: null }

  return {
    client: () => fakeClient,
    getSession: () => session,
    getToken: () => session.token
  }
}

interface FakeClientData {
  lastPost: { options?: BackendRequestOptions; path: string } | null
  post: (path: string, options?: BackendRequestOptions) => Promise<unknown>
}

function makeFakeClient(overrides: { result?: unknown; throw?: Error } = {}): FakeClientData & BackendClient {
  const fake = {
    baseUrl: 'https://api.test.com',
    delete: async <T = unknown>() => ({}) as T,
    get: async <T = unknown>() => ({}) as T,
    lastPost: null as { options?: BackendRequestOptions; path: string } | null,
    patch: async <T = unknown>() => ({}) as T,
    async post<T = unknown>(path: string, options?: BackendRequestOptions): Promise<T> {
      fake.lastPost = { options, path }

      if (overrides.throw) {
        throw overrides.throw
      }

      return (overrides.result ?? { content: 'response', usage: null }) as T
    },
    put: async <T = unknown>() => ({}) as T,
    request: async <T = unknown>() => ({}) as T
  }

  return fake
}

let fakeClient: FakeClientData & BackendClient

test('throws when backendSession is missing', () => {
  assert.throws(() => createReverseRpc({}), /backendSession/)
})

test('handleRequest() throws for unknown method', async () => {
  const handler = createReverseRpc({
    backendSession: makeFakeSession(),
    log: () => {}
  })

  await assert.rejects(
    handler('unknown_method', {}),
    (err: unknown) => err instanceof Error && /Unknown reverse RPC/.test(err.message)
  )
})

test('handleRequest() attaches -32601 code for unknown method', async () => {
  const handler = createReverseRpc({
    backendSession: makeFakeSession(),
    log: () => {}
  })

  await assert.rejects(handler('unknown_method', {}), (err: unknown) => (err as { code?: number })?.code === -32601)
})

test('handleRequest() throws when no session', async () => {
  const handler = createReverseRpc({
    backendSession: makeFakeSession({ hasToken: false }),
    log: () => {}
  })

  await assert.rejects(
    handler('request_llm', { messages: [] }),
    (err: unknown) => err instanceof Error && /No active session/.test(err.message)
  )
})

test('handleRequest() calls client().post() with correct params', async () => {
  fakeClient = makeFakeClient()

  const handler = createReverseRpc({
    backendSession: makeFakeSession({ baseUrl: 'https://api.example.com', token: 'my-token' }),
    log: () => {}
  })

  const result = (await handler('request_llm', {
    max_tokens: 256,
    messages: [{ content: 'hello', role: 'user' }],
    model: 'gpt-x',
    temperature: 0.7
  })) as { content?: string }

  assert.equal(fakeClient.lastPost?.path, '/api/llm/completion')
  assert.equal(fakeClient.lastPost?.options?.token, 'my-token')
  const body = fakeClient.lastPost?.options?.body as Record<string, unknown>
  assert.deepEqual(body?.input, [{ role: 'user', content: [{ type: 'input_text', text: 'hello' }] }])
  assert.equal(body?.instructions, undefined)
  assert.equal(body?.model, 'gpt-x')
  assert.equal(body?.temperature, 0.7)
  assert.equal(body?.max_output_tokens, 256)
  assert.ok(Number.isFinite(fakeClient.lastPost?.options?.timeoutMs))
  assert.equal(result.content, 'response')
})

test('handleRequest() maps tool trajectories to Responses input items', async () => {
  fakeClient = makeFakeClient()

  const handler = createReverseRpc({
    backendSession: makeFakeSession(),
    log: () => {}
  })

  await handler('request_llm', {
    messages: [
      { content: 'sys', role: 'system' },
      { content: 'look', role: 'user' },
      {
        content: undefined,
        role: 'assistant',
        tool_calls: [{ id: 'call_1', function: { arguments: '{}', name: 'demo' } }]
      },
      { content: 'result', role: 'tool', tool_call_id: 'call_1' }
    ]
  })

  const body = fakeClient.lastPost?.options?.body as Record<string, unknown>
  assert.equal(body?.instructions, 'sys')
  assert.deepEqual(body?.input, [
    { role: 'user', content: [{ type: 'input_text', text: 'look' }] },
    { arguments: '{}', call_id: 'call_1', name: 'demo', type: 'function_call' },
    { call_id: 'call_1', output: 'result', type: 'function_call_output' }
  ])
})

test('handleRequest() sends native Responses payloads without message conversion', async () => {
  fakeClient = makeFakeClient()

  const handler = createReverseRpc({
    backendSession: makeFakeSession(),
    log: () => {}
  })

  await handler('request_llm', {
    input: [{ role: 'user', content: [{ type: 'input_text', text: 'hello' }] }],
    instructions: 'sys'
  })

  const body = fakeClient.lastPost?.options?.body as Record<string, unknown>
  assert.equal(body?.instructions, 'sys')
  assert.deepEqual(body?.input, [{ role: 'user', content: [{ type: 'input_text', text: 'hello' }] }])
})

test('handleRequest() propagates client errors', async () => {
  const networkError = new Error('connect ECONNREFUSED')
  fakeClient = makeFakeClient({ throw: networkError })

  const handler = createReverseRpc({
    backendSession: makeFakeSession(),
    log: () => {}
  })

  await assert.rejects(handler('request_llm', { messages: [] }), (err: unknown) => err === networkError)
})
