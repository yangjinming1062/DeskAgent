const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const { createBackendSession, SessionError, decodeActivationCode } = require('./session.cjs')

function tmpUserData(tag) {
  return fs.mkdtempSync(path.join(os.tmpdir(), `deskagent-session-test-${tag}-`))
}

// Minimal safeStorage stub: encrypt is identity, decrypt is identity.
// We don't need real crypto to verify the persist+restore path runs.
function identitySafeStorage() {
  return {
    isEncryptionAvailable: () => true,
    encryptString: value => Buffer.from(String(value), 'utf8'),
    decryptString: buf => buf.toString('utf8')
  }
}

/** Encode ``{b, t}`` into a base64url activation code (mirrors backend). */
function encodeActivationCode(baseUrl, token) {
  const payload = JSON.stringify({ b: baseUrl, t: token })
  return Buffer.from(payload, 'utf8').toString('base64url')
}

/** Build a fake fetchImpl that responds to POST /api/user/activate. */
function fakeActivateFetch(response) {
  const body = JSON.stringify(response)
  return async (url, options = {}) => {
    if (typeof url !== 'string' || !url.includes('/api/user/activate')) {
      throw new Error(`unexpected fetch: ${url}`)
    }
    return {
      ok: true,
      status: 200,
      headers: { get: () => 'application/json' },
      text: async () => body
    }
  }
}

const TOKEN_RESPONSE = {
  access_token: 'jwt-session-token',
  expires_in: 3600,
  user: { id: 9, username: 'carol' }
}

test('activate stores the session and returns a snapshot', async () => {
  const userDataDir = tmpUserData('activate')
  const code = encodeActivationCode('https://api.example.com', 'raw-activation-token')
  const session = createBackendSession({
    userDataDir,
    safeStorage: identitySafeStorage(),
    appVersion: 'test',
    fetchImpl: fakeActivateFetch(TOKEN_RESPONSE),
    now: () => 1_000_000
  })

  const result = await session.activate({ code })

  assert.equal(result.baseUrl, 'https://api.example.com')
  assert.equal(result.hasToken, true)
  assert.equal(result.user.username, 'carol')
  assert.equal(session.getToken(), 'jwt-session-token')
})

test('activate persists the activation code so restoreSession can re-activate', async () => {
  const userDataDir = tmpUserData('persist')
  const code = encodeActivationCode('https://api.example.com', 'raw-activation-token')
  const session = createBackendSession({
    userDataDir,
    safeStorage: identitySafeStorage(),
    appVersion: 'test',
    fetchImpl: fakeActivateFetch(TOKEN_RESPONSE)
  })

  await session.activate({ code })

  // Build a fresh session against the same userDataDir to simulate
  // an app restart.  restoreSession() is now async — it calls
  // /api/user/activate with the stored code to obtain a fresh JWT.
  const restored = createBackendSession({
    userDataDir,
    safeStorage: identitySafeStorage(),
    appVersion: 'test',
    fetchImpl: fakeActivateFetch(TOKEN_RESPONSE)
  })

  const snapshot = await restored.restoreSession()
  assert.ok(snapshot)
  assert.equal(snapshot.baseUrl, 'https://api.example.com')
  assert.equal(snapshot.hasToken, true)
  assert.equal(snapshot.user.username, 'carol')
})

test('activate rejects missing code', () => {
  const userDataDir = tmpUserData('bad-code')
  const session = createBackendSession({
    userDataDir,
    safeStorage: identitySafeStorage(),
    appVersion: 'test',
    fetchImpl: async () => ({})
  })

  assert.throws(
    () => session.activate({}),
    err => err instanceof SessionError && err.code === 'missing-code'
  )
})

test('activate rejects malformed code', () => {
  const userDataDir = tmpUserData('malformed')
  const session = createBackendSession({
    userDataDir,
    safeStorage: identitySafeStorage(),
    appVersion: 'test',
    fetchImpl: async () => ({})
  })

  assert.throws(
    () => session.activate({ code: '!!!not-valid-base64!!!' }),
    err => err instanceof SessionError && err.code === 'invalid-code'
  )
})

test('decodeActivationCode round-trips baseUrl and token', () => {
  const code = encodeActivationCode('http://localhost:10620', 'abc-123')
  const decoded = decodeActivationCode(code)
  assert.equal(decoded.baseUrl, 'http://localhost:10620')
  assert.equal(decoded.token, 'abc-123')
})
