/**
 * Tests for electron/runner-reverse-rpc.cjs.
 *
 * Run with: node --test electron/runner-reverse-rpc.test.cjs
 */

const test = require('node:test')
const assert = require('node:assert/strict')
const { createReverseRpc } = require('./runner-reverse-rpc.cjs')

function makeFakeSession({ token = 'test-token', baseUrl = 'https://api.test.com', hasToken = true } = {}) {
  const session = hasToken ? { hasToken: true, baseUrl, token } : { hasToken: false }
  return {
    getSession: () => session,
    // client() returns a fake that records the last call; tests assert against it.
    client: () => fakeClient
  }
}

function makeFakeClient(overrides = {}) {
  const fake = {
    lastPost: null,
    async post(path, options) {
      fake.lastPost = { path, options }
      if (overrides.throw) throw overrides.throw
      return overrides.result ?? { content: 'response', usage: null }
    }
  }
  return fake
}

let fakeClient

test('throws when backendSession is missing', () => {
  assert.throws(() => createReverseRpc({}), /backendSession/)
})

test('handleRequest() throws for unknown method', async () => {
  const handler = createReverseRpc({
    backendSession: makeFakeSession(),
    log: () => {}
  })
  await assert.rejects(handler('unknown_method', {}), err => /Unknown reverse RPC/.test(err.message))
})

test('handleRequest() attaches -32601 code for unknown method', async () => {
  const handler = createReverseRpc({
    backendSession: makeFakeSession(),
    log: () => {}
  })
  await assert.rejects(handler('unknown_method', {}), err => err.code === -32601)
})

test('handleRequest() throws when no session', async () => {
  const handler = createReverseRpc({
    backendSession: makeFakeSession({ hasToken: false }),
    log: () => {}
  })
  await assert.rejects(handler('request_llm', { messages: [] }), err => /No active session/.test(err.message))
})

test('handleRequest() calls client().post() with correct params', async () => {
  fakeClient = makeFakeClient()
  const handler = createReverseRpc({
    backendSession: makeFakeSession({ token: 'my-token', baseUrl: 'https://api.example.com' }),
    log: () => {}
  })

  const result = await handler('request_llm', {
    messages: [{ role: 'user', content: 'hello' }],
    model: 'gpt-x',
    temperature: 0.7,
    max_tokens: 256
  })

  assert.equal(fakeClient.lastPost.path, '/api/llm/completion')
  assert.equal(fakeClient.lastPost.options.token, 'my-token')
  assert.deepEqual(fakeClient.lastPost.options.body.messages, [{ role: 'user', content: 'hello' }])
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
  await assert.rejects(handler('request_llm', { messages: [] }), err => err === networkError)
})
