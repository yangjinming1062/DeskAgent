import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { pathToFileURL } from 'node:url'

import {
  AVATAR_FETCH_TIMEOUT_MS,
  DEFAULT_CSP_POLICY,
  DEFAULT_FETCH_TIMEOUT_MS,
  resolvePathTimeoutMs,
  resolveReadableFileForIpc,
  sensitiveFileBlockReason
} from './hardening'

test('resolvePathTimeoutMs routes avatar and generative AI POSTs to the slow bucket', () => {
  assert.equal(resolvePathTimeoutMs('/api/companion/avatar', 'POST'), AVATAR_FETCH_TIMEOUT_MS)
  assert.equal(resolvePathTimeoutMs('/api/companion/avatar/from-image', 'POST'), AVATAR_FETCH_TIMEOUT_MS)
  assert.equal(resolvePathTimeoutMs('/api/companion/avatar/1/fullbody/samples', 'POST'), AVATAR_FETCH_TIMEOUT_MS)
  assert.equal(resolvePathTimeoutMs('/api/companion/avatar/1/fullbody/front', 'POST'), AVATAR_FETCH_TIMEOUT_MS)
  assert.equal(resolvePathTimeoutMs('/api/companion/avatar/1/fullbody/back', 'POST'), AVATAR_FETCH_TIMEOUT_MS)
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
  assert.equal(resolvePathTimeoutMs(42 as unknown as string, 'POST'), DEFAULT_FETCH_TIMEOUT_MS)
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

test('DEFAULT_CSP_POLICY enforces strict directives and allows necessary protocols', () => {
  assert.ok(DEFAULT_CSP_POLICY.includes("default-src 'self'"))
  assert.ok(DEFAULT_CSP_POLICY.includes("script-src 'self' 'wasm-unsafe-eval'"))
  assert.ok(!DEFAULT_CSP_POLICY.includes("script-src 'self' 'unsafe-inline'"))
  assert.ok(DEFAULT_CSP_POLICY.includes("style-src 'self' 'unsafe-inline'"))
  assert.ok(DEFAULT_CSP_POLICY.includes('spiritagent-media:'))
  assert.ok(DEFAULT_CSP_POLICY.includes("object-src 'none'"))
  assert.ok(DEFAULT_CSP_POLICY.includes("base-uri 'self'"))
  assert.ok(DEFAULT_CSP_POLICY.includes("form-action 'none'"))
})

test('HTML entries contain valid Content-Security-Policy meta tag', () => {
  const htmlFiles = ['index.html', 'sprite.html', 'hub.html', 'clip-debugger.html']

  for (const file of htmlFiles) {
    const htmlPath = path.resolve(import.meta.dirname, '..', '..', file)
    const html = fs.readFileSync(htmlPath, 'utf8')
    assert.ok(html.includes('http-equiv="Content-Security-Policy"'), `${file} must have CSP meta tag`)
    assert.ok(html.includes("default-src 'self'"), `${file} CSP meta tag must define default-src`)
    assert.ok(html.includes("script-src 'self' 'wasm-unsafe-eval'"), `${file} CSP meta tag must harden script-src`)
    assert.ok(html.includes('spiritagent-media:'), `${file} CSP meta tag must allow spiritagent-media`)
  }
})
