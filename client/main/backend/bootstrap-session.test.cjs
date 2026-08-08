const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const {
  SCHEMA_VERSION,
  FILENAME,
  CONSUMED_SUFFIX,
  BOOTSTRAP_ENV_VAR,
  defaultBootstrapPath,
  readBootstrapFile,
  validateViaRefresh,
  consumeBootstrapSession
} = require('./bootstrap-session.cjs')

function tmpHome(tag) {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), `deskagent-bootstrap-test-${tag}-`))
  return base
}

function writeBootstrap(home, payload) {
  const target = defaultBootstrapPath(home)
  fs.writeFileSync(target, JSON.stringify(payload, null, 2), 'utf8')
  return target
}

function validPayload(overrides = {}) {
  return {
    schemaVersion: SCHEMA_VERSION,
    baseUrl: 'https://api.example.com',
    token: 'jwt-original',
    tokenExpiresAt: Date.now() + 60_000,
    user: { id: 42, username: 'alice' },
    savedAt: Date.now(),
    ...overrides
  }
}

test('readBootstrapFile returns missing when no file', () => {
  const home = tmpHome('missing')
  const result = readBootstrapFile(defaultBootstrapPath(home))
  assert.equal(result.ok, false)
  assert.equal(result.code, 'missing')
})

test('readBootstrapFile rejects schema mismatch', () => {
  const home = tmpHome('schema')
  writeBootstrap(home, { ...validPayload(), schemaVersion: SCHEMA_VERSION + 9 })
  const result = readBootstrapFile(defaultBootstrapPath(home))
  assert.equal(result.ok, false)
  assert.equal(result.code, 'schema-mismatch')
})

test('readBootstrapFile rejects malformed JSON', () => {
  const home = tmpHome('malformed')
  fs.writeFileSync(defaultBootstrapPath(home), '{ not json', 'utf8')
  const result = readBootstrapFile(defaultBootstrapPath(home))
  assert.equal(result.ok, false)
  assert.equal(result.code, 'malformed')
})

test('readBootstrapFile rejects missing token', () => {
  const home = tmpHome('notoken')
  writeBootstrap(home, validPayload({ token: '' }))
  const result = readBootstrapFile(defaultBootstrapPath(home))
  assert.equal(result.ok, false)
  assert.equal(result.code, 'missing-token')
})

test('readBootstrapFile rejects missing baseUrl', () => {
  const home = tmpHome('nourl')
  writeBootstrap(home, validPayload({ baseUrl: '' }))
  const result = readBootstrapFile(defaultBootstrapPath(home))
  assert.equal(result.ok, false)
  assert.equal(result.code, 'missing-base-url')
})

test('readBootstrapFile accepts valid payload', () => {
  const home = tmpHome('ok')
  writeBootstrap(home, validPayload())
  const result = readBootstrapFile(defaultBootstrapPath(home))
  assert.equal(result.ok, true)
  assert.equal(result.session.baseUrl, 'https://api.example.com')
  assert.equal(result.session.token, 'jwt-original')
  assert.equal(result.session.user.username, 'alice')
})

test('consumeBootstrapSession honors DESKAGENT_DESKTOP_BOOTSTRAP_SESSION env override', async () => {
  const home = tmpHome('env')
  const alt = path.join(tmpHome('env-alt'), FILENAME)
  fs.writeFileSync(alt, JSON.stringify(validPayload()), 'utf8')

  const fetchImpl = async () => ({
    ok: true,
    status: 200,
    json: async () => ({
      access_token: 'jwt-refreshed',
      expires_in: 3600,
      user: { id: 42, username: 'alice' }
    })
  })

  const result = await consumeBootstrapSession({
    deskagentHome: home,
    fetchImpl,
    env: { [BOOTSTRAP_ENV_VAR]: alt }
  })

  assert.equal(result.status, 'ok')
  assert.equal(result.snapshot.token, 'jwt-refreshed')
  assert.equal(result.snapshot.baseUrl, 'https://api.example.com')
  // Override file is claimed to .consumed, leaving default path untouched.
  assert.ok(fs.existsSync(`${alt}${CONSUMED_SUFFIX}`), 'override file should be claimed')
  assert.ok(!fs.existsSync(defaultBootstrapPath(home)), 'home path should be untouched')
})

test('consumeBootstrapSession fails back to unauthenticated when refresh rejects', async () => {
  const home = tmpHome('refresh-fail')
  writeBootstrap(home, validPayload())

  const fetchImpl = async () => ({
    ok: false,
    status: 401,
    json: async () => ({ detail: 'expired' })
  })

  const result = await consumeBootstrapSession({
    deskagentHome: home,
    fetchImpl
  })

  assert.equal(result.status, 'refresh-failed')
  assert.equal(result.snapshot, null)
  // File was claimed (renamed to .consumed) so a second launch can't replay it.
  assert.ok(fs.existsSync(`${defaultBootstrapPath(home)}${CONSUMED_SUFFIX}`))
})

test('consumeBootstrapSession returns ok on a successful refresh', async () => {
  const home = tmpHome('refresh-ok')
  writeBootstrap(home, validPayload())

  const fetchImpl = async () => ({
    ok: true,
    status: 200,
    json: async () => ({
      access_token: 'jwt-refreshed',
      expires_in: 7200,
      user: { id: 7, username: 'bob' }
    })
  })

  const result = await consumeBootstrapSession({
    deskagentHome: home,
    fetchImpl
  })

  assert.equal(result.status, 'ok')
  assert.equal(result.snapshot.token, 'jwt-refreshed')
  assert.equal(result.snapshot.user.username, 'bob')
  assert.ok(Number.isFinite(result.snapshot.tokenExpiresAt))
  assert.ok(result.snapshot.tokenExpiresAt > Date.now())
})

test('consumeBootstrapSession returns missing when there is no file', async () => {
  const home = tmpHome('absent')
  const result = await consumeBootstrapSession({ deskagentHome: home, fetchImpl: async () => ({}) })
  assert.equal(result.status, 'missing')
})

test('consumeBootstrapSession treats malformed file as invalid and cleans up', async () => {
  const home = tmpHome('bad')
  const target = defaultBootstrapPath(home)
  fs.writeFileSync(target, 'not-json', 'utf8')

  const result = await consumeBootstrapSession({ deskagentHome: home, fetchImpl: async () => ({}) })
  assert.equal(result.status, 'invalid')
  assert.equal(result.code, 'malformed')
  assert.ok(!fs.existsSync(target), 'malformed bootstrap file should be deleted')
})

test('validateViaRefresh returns null on non-ok status', async () => {
  const result = await validateViaRefresh('https://api.example.com', 'jwt', async () => ({
    ok: false,
    status: 500,
    json: async () => ({})
  }))
  assert.equal(result, null)
})

test('validateViaRefresh returns null when access_token missing', async () => {
  const result = await validateViaRefresh('https://api.example.com', 'jwt', async () => ({
    ok: true,
    status: 200,
    json: async () => ({ expires_in: 3600 })
  }))
  assert.equal(result, null)
})

test('validateViaRefresh returns null when fetch throws', async () => {
  const result = await validateViaRefresh('https://api.example.com', 'jwt', async () => {
    throw new Error('offline')
  })
  assert.equal(result, null)
})

test('validateViaRefresh returns null when fetchImpl is not a function', async () => {
  const result = await validateViaRefresh('https://api.example.com', 'jwt', null)
  assert.equal(result, null)
})

// Drift guard: the Rust side at installer/src-tauri/src/paths.rs is the
// canonical source for FILENAME / SCHEMA_VERSION / CONSUMED_SUFFIX — if
// any one drifts, the JS side silently rejects every new file (or accepts
// every new file). This test reads paths.rs and asserts the JS mirror
// matches, catching the drift deterministically on test failure rather
// than at the user's first launch.
test('bootstrap_constants_match_rust_paths', () => {
  const pathsFile = path.resolve(__dirname, '..', '..', '..', 'installer', 'src-tauri', 'src', 'paths.rs')
  const source = fs.readFileSync(pathsFile, 'utf8')

  const expect = re => {
    const match = source.match(re)
    if (!match) {
      throw new Error(`paths.rs is missing the expected constant; is the source layout unchanged? (${re})`)
    }
    return match[1]
  }

  // Strip trailing semicolon + quotes from a `pub const NAME: &str = "value";`
  // or `pub const NAME: u32 = value;` declaration.
  const rustString = re => expect(re).replace(/^"|"$/g, '')
  const rustU32 = re => Number(expect(re))

  assert.equal(
    rustString(/pub const BOOTSTRAP_FILENAME:\s*&str\s*=\s*"([^"]+)"\s*;/),
    FILENAME,
    'BOOTSTRAP_FILENAME in paths.rs does not match FILENAME in bootstrap-session.cjs'
  )
  assert.equal(
    rustString(/pub const BOOTSTRAP_CONSUMED_SUFFIX:\s*&str\s*=\s*"([^"]+)"\s*;/),
    CONSUMED_SUFFIX,
    'BOOTSTRAP_CONSUMED_SUFFIX in paths.rs does not match CONSUMED_SUFFIX in bootstrap-session.cjs'
  )
  assert.equal(
    rustU32(/pub const BOOTSTRAP_SCHEMA_VERSION:\s*u32\s*=\s*(\d+)\s*;/),
    SCHEMA_VERSION,
    'BOOTSTRAP_SCHEMA_VERSION in paths.rs does not match SCHEMA_VERSION in bootstrap-session.cjs'
  )
})
