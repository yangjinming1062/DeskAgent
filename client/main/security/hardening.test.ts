import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { pathToFileURL } from 'node:url'

import {
  AVATAR_FETCH_TIMEOUT_MS,
  DEFAULT_FETCH_TIMEOUT_MS,
  resolvePathTimeoutMs,
  resolveReadableFileForIpc,
  resolveTimeoutMs,
  sensitiveFileBlockReason
} from './hardening'

test('resolveTimeoutMs falls back to defaults and accepts overrides', () => {
  assert.equal(resolveTimeoutMs(undefined), DEFAULT_FETCH_TIMEOUT_MS)
  assert.equal(resolveTimeoutMs(0), DEFAULT_FETCH_TIMEOUT_MS)
  assert.equal(resolveTimeoutMs(-25), DEFAULT_FETCH_TIMEOUT_MS)
  assert.equal(resolveTimeoutMs('2750'), 2750)
})

test('resolvePathTimeoutMs routes avatar and generative AI POSTs to the slow bucket', () => {
  assert.equal(resolvePathTimeoutMs('/api/companion/avatar', 'POST'), AVATAR_FETCH_TIMEOUT_MS)
  assert.equal(resolvePathTimeoutMs('/api/companion/avatar/from-image', 'POST'), AVATAR_FETCH_TIMEOUT_MS)
  assert.equal(resolvePathTimeoutMs('/api/companion/avatar/1/fullbody/samples', 'POST'), AVATAR_FETCH_TIMEOUT_MS)
  assert.equal(resolvePathTimeoutMs('/api/companion/avatar/1/fullbody/front', 'POST'), AVATAR_FETCH_TIMEOUT_MS)
  assert.equal(resolvePathTimeoutMs('/api/companion/avatar/1/fullbody/confirm-front', 'POST'), AVATAR_FETCH_TIMEOUT_MS)
  assert.equal(resolvePathTimeoutMs('/API/COMPANION/AVATAR', 'post'), AVATAR_FETCH_TIMEOUT_MS)
  assert.equal(resolvePathTimeoutMs('/api/companion/sprite', 'POST'), AVATAR_FETCH_TIMEOUT_MS)
  assert.equal(resolvePathTimeoutMs('/api/media/image_gen', 'POST'), AVATAR_FETCH_TIMEOUT_MS)
})

test('resolvePathTimeoutMs keeps reads and PUTs on the fast default', () => {
  assert.equal(resolvePathTimeoutMs('/api/companion/avatar', 'GET'), DEFAULT_FETCH_TIMEOUT_MS)
  assert.equal(resolvePathTimeoutMs('/api/companion/avatar', 'PUT'), DEFAULT_FETCH_TIMEOUT_MS)
  assert.equal(resolvePathTimeoutMs('/api/companion/avatar/history', 'GET'), DEFAULT_FETCH_TIMEOUT_MS)
})

test('resolvePathTimeoutMs ignores unrelated paths', () => {
  assert.equal(resolvePathTimeoutMs('/api/config', 'POST'), DEFAULT_FETCH_TIMEOUT_MS)
  assert.equal(resolvePathTimeoutMs('/api/config', 'PUT'), DEFAULT_FETCH_TIMEOUT_MS)
  assert.equal(resolvePathTimeoutMs('/api/companion/persona', 'PUT'), DEFAULT_FETCH_TIMEOUT_MS)
  assert.equal(resolvePathTimeoutMs('/api/companion/voices', 'GET'), DEFAULT_FETCH_TIMEOUT_MS)
})

test('resolvePathTimeoutMs tolerates garbage inputs', () => {
  assert.equal(resolvePathTimeoutMs(undefined, 'POST'), DEFAULT_FETCH_TIMEOUT_MS)
  assert.equal(resolvePathTimeoutMs('', 'POST'), DEFAULT_FETCH_TIMEOUT_MS)
  assert.equal(resolvePathTimeoutMs(null, 'POST'), DEFAULT_FETCH_TIMEOUT_MS)
  assert.equal(resolvePathTimeoutMs(42 as any, 'POST'), DEFAULT_FETCH_TIMEOUT_MS)
})

test('resolvePathTimeoutMs honors a custom fallbackMs', () => {
  assert.equal(resolvePathTimeoutMs('/api/config', 'POST', 9_000), 9_000)
  assert.equal(resolvePathTimeoutMs('/api/companion/avatar', 'POST', 9_000), AVATAR_FETCH_TIMEOUT_MS)
})

test('sensitiveFileBlockReason blocks obvious secret file patterns', () => {
  assert.match(String(sensitiveFileBlockReason('/tmp/.env')), /\.env/)
  assert.equal(sensitiveFileBlockReason('/tmp/.env.example'), null)
  assert.match(String(sensitiveFileBlockReason('/Users/me/.ssh/id_ed25519')), /SSH/)
  assert.match(String(sensitiveFileBlockReason('/tmp/server-cert.pem')), /\.pem/)
})

test('resolveReadableFileForIpc validates existence type size and sensitivity', async t => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'spiritagent-desktop-hardening-'))
  t.after(() => fs.rmSync(tempDir, { force: true, recursive: true }))

  const textPath = path.join(tempDir, 'notes.txt')
  fs.writeFileSync(textPath, 'hello world', 'utf8')

  const fromRelative = await resolveReadableFileForIpc('notes.txt', {
    baseDir: tempDir,
    maxBytes: 256,
    purpose: 'File preview'
  })

  assert.equal(fromRelative.resolvedPath, textPath)
  assert.equal(fromRelative.stat.size, 11)

  const fromFileUrl = await resolveReadableFileForIpc(pathToFileURL(textPath).toString(), {
    purpose: 'File preview'
  })

  assert.equal(fromFileUrl.resolvedPath, textPath)

  await assert.rejects(
    resolveReadableFileForIpc('missing.txt', {
      baseDir: tempDir,
      purpose: 'Text preview'
    }),
    /file does not exist/
  )

  const nestedDir = path.join(tempDir, 'directory')
  fs.mkdirSync(nestedDir)
  await assert.rejects(
    resolveReadableFileForIpc(nestedDir, {
      purpose: 'Text preview'
    }),
    /path points to a directory/
  )

  const largePath = path.join(tempDir, 'large.txt')
  fs.writeFileSync(largePath, 'x'.repeat(40), 'utf8')
  await assert.rejects(
    resolveReadableFileForIpc(largePath, {
      maxBytes: 8,
      purpose: 'File preview'
    }),
    /file is too large/
  )

  const envPath = path.join(tempDir, '.env')
  fs.writeFileSync(envPath, 'SECRET_TOKEN=123', 'utf8')
  await assert.rejects(
    resolveReadableFileForIpc(envPath, {
      purpose: 'File preview'
    }),
    /blocked for sensitive file/
  )

  const envTemplatePath = path.join(tempDir, '.env.example')
  fs.writeFileSync(envTemplatePath, 'EXAMPLE_TOKEN=value', 'utf8')

  const envTemplate = await resolveReadableFileForIpc(envTemplatePath, {
    purpose: 'File preview'
  })

  assert.equal(envTemplate.resolvedPath, envTemplatePath)
})
