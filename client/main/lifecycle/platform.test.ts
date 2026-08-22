import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'

import { detectRemoteDisplay } from './platform'

test('detectRemoteDisplay keeps GPU on for local sessions', () => {
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

test('detectRemoteDisplay honors the SPIRITAGENT_DESKTOP_DISABLE_GPU override both ways', () => {
  assert.match(
    String(detectRemoteDisplay({ env: { SPIRITAGENT_DESKTOP_DISABLE_GPU: '1' }, platform: 'darwin' })),
    /override/
  )
  assert.equal(
    detectRemoteDisplay({
      env: { SPIRITAGENT_DESKTOP_DISABLE_GPU: 'false', SSH_CONNECTION: '1.2.3.4 5 6.7.8.9 22' },
      platform: 'win32'
    }),
    null
  )
})

test('packaged electron entrypoints do not require unpackaged npm modules', () => {
  const electronDir = path.join(import.meta.dirname, '..')
  const entrypoints = ['entry.ts', 'preload.ts', 'lifecycle/platform.ts']

  // `@ipc/contracts` 是 tsconfig paths + tsup esbuildOptions 别名,
  // 解析到 `client/shared/ipc/contracts.ts` 本地文件,不需要 npm 打包。
  const allowedBareRequires = new Set([
    'electron',
    'electron-updater',
    'electron-log',
    'electron-log/main',
    'ws',
    '@ipc/contracts'
  ])

  const requirePattern = /(?:require\(|from\s+)['"]([^'"]+)['"]/g

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
