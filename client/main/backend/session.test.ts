import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import { createBackendSession, decodeActivationCode, SessionError } from './session'

function tmpUserData(tag: string): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), `spiritagent-session-test-${tag}-`))
}

function identitySafeStorage() {
  return {
    decryptString: (buf: Buffer) => buf.toString('utf8'),
    encryptString: (value: string) => Buffer.from(String(value), 'utf8'),
    isEncryptionAvailable: () => true
  }
}

function encodeActivationCode(baseUrl: string, token: string): string {
  const payload = JSON.stringify({ b: baseUrl, t: token })

  return Buffer.from(payload, 'utf8').toString('base64url')
}

function fakeActivateFetch(response: unknown) {
  const body = JSON.stringify(response)

  return async (url: string) => {
    if (typeof url !== 'string' || !url.includes('/api/user/activate')) {
      throw new Error(`unexpected fetch: ${url}`)
    }

    return {
      headers: { get: () => 'application/json' },
      ok: true,
      status: 200,
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
    appVersion: 'test',
    fetchImpl: fakeActivateFetch(TOKEN_RESPONSE),
    now: () => 1_000_000,
    safeStorage: identitySafeStorage(),
    userDataDir
  })

  const result = await session.activate({ code })

  assert.equal(result?.baseUrl, 'https://api.example.com')
  assert.equal(result?.hasToken, true)
  assert.equal(result?.user?.username, 'carol')
  assert.equal(session.getToken(), 'jwt-session-token')
})

test('activate persists the activation code so restoreSession can re-activate', async () => {
  const userDataDir = tmpUserData('persist')
  const code = encodeActivationCode('https://api.example.com', 'raw-activation-token')

  const session = createBackendSession({
    appVersion: 'test',
    fetchImpl: fakeActivateFetch(TOKEN_RESPONSE),
    safeStorage: identitySafeStorage(),
    userDataDir
  })

  await session.activate({ code })

  const restored = createBackendSession({
    appVersion: 'test',
    fetchImpl: fakeActivateFetch(TOKEN_RESPONSE),
    safeStorage: identitySafeStorage(),
    userDataDir
  })

  const snapshot = await restored.restoreSession()
  assert.ok(snapshot)
  assert.equal(snapshot.baseUrl, 'https://api.example.com')
  assert.equal(snapshot.hasToken, true)
  assert.equal(snapshot.user?.username, 'carol')
})

test('activate rejects missing code', async () => {
  const userDataDir = tmpUserData('bad-code')

  const session = createBackendSession({
    appVersion: 'test',
    fetchImpl: async () => ({
      headers: { get: () => 'application/json' },
      ok: true,
      status: 200,
      text: async () => '{}'
    }),
    safeStorage: identitySafeStorage(),
    userDataDir
  })

  await assert.rejects(
    () => session.activate({}),
    (err: unknown) => err instanceof SessionError && err.code === 'missing-code'
  )
})

test('activate rejects malformed code', async () => {
  const userDataDir = tmpUserData('malformed')

  const session = createBackendSession({
    appVersion: 'test',
    fetchImpl: async () => ({
      headers: { get: () => 'application/json' },
      ok: true,
      status: 200,
      text: async () => '{}'
    }),
    safeStorage: identitySafeStorage(),
    userDataDir
  })

  await assert.rejects(
    () => session.activate({ code: '!!!not-valid-base64!!!' }),
    (err: unknown) => err instanceof SessionError && err.code === 'invalid-code'
  )
})

test('decodeActivationCode round-trips baseUrl and token', () => {
  const code = encodeActivationCode('http://localhost:10620', 'abc-123')
  const decoded = decodeActivationCode(code)
  assert.equal(decoded.baseUrl, 'http://localhost:10620')
  assert.equal(decoded.token, 'abc-123')
})
