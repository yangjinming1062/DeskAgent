const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const { createBackendSession, SessionError } = require('./session.cjs')

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

test('adoptSession sets the cached session and returns a snapshot', () => {
  const userDataDir = tmpUserData('adopt')
  const session = createBackendSession({
    userDataDir,
    safeStorage: identitySafeStorage(),
    appVersion: 'test',
    fetchImpl: async () => {
      throw new Error('not called')
    },
    defaultBaseUrl: null,
    now: () => 1_000_000
  })

  const result = session.adoptSession({
    baseUrl: 'https://api.example.com',
    token: 'jwt-adopted',
    tokenExpiresAt: 2_000_000,
    user: { id: 9, username: 'carol' }
  })

  assert.equal(result.baseUrl, 'https://api.example.com')
  assert.equal(result.hasToken, true)
  assert.equal(result.user.username, 'carol')
  assert.equal(session.getToken(), 'jwt-adopted')
})

test('adoptSession persists the encrypted token so the next restoreSession loads it', () => {
  const userDataDir = tmpUserData('persist')
  const session = createBackendSession({
    userDataDir,
    safeStorage: identitySafeStorage(),
    appVersion: 'test',
    fetchImpl: async () => {
      throw new Error('not called')
    }
  })

  session.adoptSession({
    baseUrl: 'https://api.example.com',
    token: 'jwt-adopted',
    tokenExpiresAt: 5_000_000,
    user: { id: 9, username: 'carol' }
  })

  // Build a fresh session against the same userDataDir to simulate
  // an app restart.
  const restored = createBackendSession({
    userDataDir,
    safeStorage: identitySafeStorage(),
    appVersion: 'test',
    fetchImpl: async () => {
      throw new Error('not called')
    }
  })

  const snapshot = restored.restoreSession()
  assert.ok(snapshot)
  assert.equal(snapshot.baseUrl, 'https://api.example.com')
  assert.equal(snapshot.hasToken, true)
  assert.equal(snapshot.user.username, 'carol')
})

test('adoptSession rejects missing token', () => {
  const userDataDir = tmpUserData('bad-token')
  const session = createBackendSession({
    userDataDir,
    safeStorage: identitySafeStorage(),
    appVersion: 'test',
    fetchImpl: async () => ({})
  })
  // Token validation lives in applySession (no-token); adoptSession is a
  // pure funnel, so it surfaces the underlying error rather than its own.
  assert.throws(
    () => session.adoptSession({ baseUrl: 'https://api.example.com', token: '' }),
    err => err instanceof SessionError && err.code === 'no-token'
  )
})
