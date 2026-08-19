import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import {
  configPath,
  FILENAME,
  readStoredBackendUrl,
  resolveNormalizedBackendUrl,
  writeStoredBackendUrl
} from './config'

function tmpHome(tag: string): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), `spiritagent-config-test-${tag}-`))
}

test('configPath lives under SPIRITAGENT_HOME', () => {
  const home = tmpHome('path')
  const target = configPath(home)
  assert.equal(target, path.join(home, FILENAME))
})

test('configPath returns null when no home provided', () => {
  assert.equal(configPath(null), null)
  assert.equal(configPath(undefined), null)
  assert.equal(configPath(''), null)
})

test('readStoredBackendUrl returns null on missing file', () => {
  const home = tmpHome('missing')
  assert.equal(readStoredBackendUrl(home), null)
})

test('readStoredBackendUrl returns null on malformed JSON', () => {
  const home = tmpHome('malformed')
  fs.writeFileSync(configPath(home)!, '{ not json', 'utf8')
  assert.equal(readStoredBackendUrl(home), null)
})

test('readStoredBackendUrl returns null when backendUrl absent', () => {
  const home = tmpHome('empty')
  fs.writeFileSync(configPath(home)!, JSON.stringify({ other: 1 }), 'utf8')
  assert.equal(readStoredBackendUrl(home), null)
})

test('writeStoredBackendUrl round-trips a URL', () => {
  const home = tmpHome('roundtrip')
  assert.equal(writeStoredBackendUrl(home, 'https://api.example.com'), true)
  assert.equal(readStoredBackendUrl(home), 'https://api.example.com')
})

test('writeStoredBackendUrl preserves other keys', () => {
  const home = tmpHome('preserve')
  fs.writeFileSync(configPath(home)!, JSON.stringify({ existing: 'value' }), 'utf8')
  writeStoredBackendUrl(home, 'https://api.example.com')
  const parsed = JSON.parse(fs.readFileSync(configPath(home)!, 'utf8'))
  assert.equal(parsed.backendUrl, 'https://api.example.com')
  assert.equal(parsed.existing, 'value')
  assert.ok(Number.isFinite(parsed.savedAt))
})

test('writeStoredBackendUrl trims whitespace', () => {
  const home = tmpHome('trim')
  writeStoredBackendUrl(home, '  https://api.example.com  ')
  assert.equal(readStoredBackendUrl(home), 'https://api.example.com')
})

test('writeStoredBackendUrl rejects empty input', () => {
  const home = tmpHome('empty-input')
  assert.equal(writeStoredBackendUrl(home, ''), false)
  assert.equal(writeStoredBackendUrl(home, '   '), false)
  assert.equal(writeStoredBackendUrl(home, null as any), false)
})

test('writeStoredBackendUrl returns false without home', () => {
  assert.equal(writeStoredBackendUrl(null as any, 'https://api.example.com'), false)
})

test('resolveNormalizedBackendUrl returns stored URL or null (trailing slash stripped)', () => {
  const home = tmpHome('resolve')
  assert.equal(resolveNormalizedBackendUrl(home), null)

  writeStoredBackendUrl(home, 'https://api.example.com/')
  assert.equal(resolveNormalizedBackendUrl(home), 'https://api.example.com')
})
