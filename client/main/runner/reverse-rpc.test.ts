import assert from 'node:assert/strict'
import test from 'node:test'

import { createReverseRpc } from './reverse-rpc'

function makeFakeSession({ baseUrl = 'https://api.test.com', hasToken = true, token = 'test-token' } = {}) {
  const session = hasToken ? { baseUrl, hasToken: true, token } : { hasToken: false }

  return {
    client: () => fakeClient,
    getSession: () => session
  }
}

function makeFakeClient(overrides: any = {}) {
  const fake = {
    lastPost: null as any,
    async post(path: string, options: any) {
      fake.lastPost = { options, path }

      if (overrides.throw) {
        throw overrides.throw
      }

      return overrides.result ?? { content: 'response', usage: null }
    }
  }

  return fake
}

let fakeClient: any

test('throws when backendSession is missing', () => {
  assert.throws(() => createReverseRpc({}), /backendSession/)
})

test('handleRequest() throws for unknown method', async () => {
  const handler = createReverseRpc({
    backendSession: makeFakeSession(),
    log: () => {}
  })

  await assert.rejects(handler('unknown_method', {}), (err: any) => /Unknown reverse RPC/.test(err.message))
})

test('handleRequest() attaches -32601 code for unknown method', async () => {
  const handler = createReverseRpc({
    backendSession: makeFakeSession(),
    log: () => {}
  })

  await assert.rejects(handler('unknown_method', {}), (err: any) => err.code === -32601)
})

test('handleRequest() throws when no session', async () => {
  const handler = createReverseRpc({
    backendSession: makeFakeSession({ hasToken: false }),
    log: () => {}
  })

  await assert.rejects(handler('request_llm', { messages: [] }), (err: any) => /No active session/.test(err.message))
})

test('handleRequest() calls client().post() with correct params', async () => {
  fakeClient = makeFakeClient()

  const handler = createReverseRpc({
    backendSession: makeFakeSession({ baseUrl: 'https://api.example.com', token: 'my-token' }),
    log: () => {}
  })

  const result = await handler('request_llm', {
    max_tokens: 256,
    messages: [{ content: 'hello', role: 'user' }],
    model: 'gpt-x',
    temperature: 0.7
  })

  assert.equal(fakeClient.lastPost.path, '/api/llm/completion')
  assert.equal(fakeClient.lastPost.options.token, 'my-token')
  assert.deepEqual(fakeClient.lastPost.options.body.messages, [{ content: 'hello', role: 'user' }])
  assert.equal(fakeClient.lastPost.options.body.model, 'gpt-x')
  assert.equal(fakeClient.lastPost.options.body.temperature, 0.7)
  assert.equal(fakeClient.lastPost.options.body.max_tokens, 256)
  assert.ok(Number.isFinite(fakeClient.lastPost.options.timeoutMs))
  assert.equal(result.content, 'response')
})

test('handleRequest() propagates client errors', async () => {
  const networkError = new Error('connect ECONNREFUSED')
  fakeClient = makeFakeClient({ throw: networkError })

  const handler = createReverseRpc({
    backendSession: makeFakeSession(),
    log: () => {}
  })

  await assert.rejects(handler('request_llm', { messages: [] }), (err: any) => err === networkError)
})
