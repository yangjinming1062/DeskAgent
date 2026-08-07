const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const { detectRemoteDisplay } = require('./platform.cjs')

test('detectRemoteDisplay keeps GPU on for local sessions', () => {
  // Plain native Windows and native macOS sessions — no remote signal.
  assert.equal(detectRemoteDisplay({ env: { SESSIONNAME: 'Console' }, platform: 'win32' }), null)
  assert.equal(detectRemoteDisplay({ env: {}, platform: 'darwin' }), null)
})

test('detectRemoteDisplay flags SSH sessions on any platform', () => {
  assert.equal(
    detectRemoteDisplay({ env: { SSH_CONNECTION: '1.2.3.4 5 6.7.8.9 22' }, platform: 'darwin' }),
    'ssh-session'
  )
  assert.equal(detectRemoteDisplay({ env: { SSH_CLIENT: '1.2.3.4 5 22' }, platform: 'darwin' }), 'ssh-session')
  assert.equal(detectRemoteDisplay({ env: { SSH_TTY: '/dev/pts/0' }, platform: 'win32' }), 'ssh-session')
})

test('detectRemoteDisplay flags RDP sessions', () => {
  assert.match(String(detectRemoteDisplay({ env: { SESSIONNAME: 'RDP-Tcp#7' }, platform: 'win32' })), /^rdp/)
})

test('detectRemoteDisplay honors the DESKAGENT_DESKTOP_DISABLE_GPU override both ways', () => {
  // Force-on.
  assert.match(
    String(detectRemoteDisplay({ env: { DESKAGENT_DESKTOP_DISABLE_GPU: '1' }, platform: 'darwin' })),
    /override/
  )
  // Force-off even over SSH (escape hatch when a remote display has working accel).
  assert.equal(
    detectRemoteDisplay({
      env: { DESKAGENT_DESKTOP_DISABLE_GPU: 'false', SSH_CONNECTION: '1.2.3.4 5 6.7.8.9 22' },
      platform: 'win32'
    }),
    null
  )
})

test('packaged electron entrypoints do not require unpackaged npm modules', () => {
  const electronDir = path.join(__dirname, '..')
  const entrypoints = ['entry.cjs', 'preload.cjs', 'lifecycle/platform.cjs']
  // - electron: provided by the electron runtime, always resolvable in packaged builds.
  // - node-pty: hoisted by workspace dedup AND shipped via extraResources to
  //   resources/native-deps/node-pty (see scripts/stage-native-deps.cjs). main.cjs
  //   has a try/catch fallback at line ~38 that resolves the staged copy when the
  //   bare require fails in the packaged asar, so the bare require itself is by
  //   design rather than an oversight.
  // - electron-updater, electron-log: declared in client/package.json
  //   `dependencies`, so electron-builder packs them into app.asar. The
  //   /main subpath on electron-log is a normal export declared in its
  //   package.json (./main.js). Both are first-party runtime deps of the
  //   auto-update channel, not dev-only tooling.
  const allowedBareRequires = new Set(['electron', 'electron-updater', 'electron-log', 'electron-log/main'])
  const requirePattern = /require\(['"]([^'"]+)['"]\)/g

  for (const entrypoint of entrypoints) {
    const source = fs.readFileSync(path.join(electronDir, entrypoint), 'utf8')
    const bareRequires = Array.from(source.matchAll(requirePattern))
      .map(match => match[1])
      .filter(specifier => !specifier.startsWith('node:'))
      .filter(specifier => !specifier.startsWith('.'))
      .filter(specifier => !allowedBareRequires.has(specifier))

    assert.deepEqual(bareRequires, [], `${entrypoint} has unpackaged runtime requires`)
  }
})
