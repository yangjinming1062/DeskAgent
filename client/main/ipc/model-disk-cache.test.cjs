'use strict'

const assert = require('node:assert/strict')
const crypto = require('node:crypto')
const fsp = require('node:fs').promises
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')
const { Readable } = require('node:stream')

const { createModelDiskCache, computeFileSha256, MAX_CACHE_FILES } = require('./model-disk-cache.cjs')

test('computeFileSha256 calculates sha256 correctly', async t => {
  const tempDir = await fsp.mkdtemp(path.join(os.tmpdir(), 'deskagent-cache-test-'))
  t.after(() => fsp.rm(tempDir, { recursive: true, force: true }))

  const filePath = path.join(tempDir, 'sample.bin')
  const data = Buffer.from('hello 3d companion model')
  await fsp.writeFile(filePath, data)

  const expectedSha = crypto.createHash('sha256').update(data).digest('hex')
  const actualSha = await computeFileSha256(filePath)
  assert.equal(actualSha, expectedSha)
})

test('model disk cache stores downloaded model and hits on subsequent calls', async t => {
  const tempDir = await fsp.mkdtemp(path.join(os.tmpdir(), 'deskagent-cache-test-'))
  t.after(() => fsp.rm(tempDir, { recursive: true, force: true }))

  const cache = createModelDiskCache({ deskagentHome: tempDir })
  const modelBytes = Buffer.from('GLB binary content 12345')
  const sha256 = crypto.createHash('sha256').update(modelBytes).digest('hex')

  let fetchCalls = 0
  const mockFetch = async () => {
    fetchCalls++
    return {
      ok: true,
      status: 200,
      headers: new Map([
        ['content-type', 'model/gltf-binary'],
        ['x-content-sha256', sha256]
      ]),
      body: Readable.from(modelBytes)
    }
  }

  // 1. Initial download (cache miss)
  const result1 = await cache.ensureCached({
    url: '/api/companion/model/file/1/model.glb',
    contentHash: sha256,
    baseUrl: 'http://127.0.0.1:8000',
    fetchFn: mockFetch
  })

  assert.equal(result1.fromCache, false)
  assert.equal(result1.contentHash, sha256)
  assert.equal(fetchCalls, 1)
  assert.equal(await cache.has(sha256), true)

  const fileContent = await fsp.readFile(result1.filePath)
  assert.deepEqual(fileContent, modelBytes)

  // 2. Second call with same contentHash (cache hit)
  const result2 = await cache.ensureCached({
    url: '/api/companion/model/file/1/model.glb',
    contentHash: sha256,
    baseUrl: 'http://127.0.0.1:8000',
    fetchFn: mockFetch
  })

  assert.equal(result2.fromCache, true)
  assert.equal(result2.contentHash, sha256)
  assert.equal(result2.filePath, result1.filePath)
  assert.equal(fetchCalls, 1) // No second fetch
})

test('model disk cache supports Range resumable download', async t => {
  const tempDir = await fsp.mkdtemp(path.join(os.tmpdir(), 'deskagent-cache-test-'))
  t.after(() => fsp.rm(tempDir, { recursive: true, force: true }))

  const cache = createModelDiskCache({ deskagentHome: tempDir })
  const fullBytes = Buffer.from('abcdefghijklmnopqrstuvwxyz0123456789')
  const sha256 = crypto.createHash('sha256').update(fullBytes).digest('hex')

  // Simulate an interrupted download: 10 bytes already in partial file
  const partialPath = cache.getPartialPath(sha256)
  await fsp.mkdir(path.dirname(partialPath), { recursive: true })
  await fsp.writeFile(partialPath, fullBytes.subarray(0, 10))

  let receivedRangeHeader = null
  const mockFetch = async (url, opts) => {
    receivedRangeHeader = opts?.headers?.['Range'] || null
    // Mock server handles Range: bytes=10-
    const remaining = fullBytes.subarray(10)
    return {
      ok: true,
      status: 206,
      headers: new Map([
        ['content-type', 'model/gltf-binary'],
        ['content-range', `bytes 10-${fullBytes.length - 1}/${fullBytes.length}`],
        ['x-content-sha256', sha256]
      ]),
      body: Readable.from(remaining)
    }
  }

  const result = await cache.ensureCached({
    url: '/api/companion/model/file/1/model.glb',
    contentHash: sha256,
    baseUrl: 'http://127.0.0.1:8000',
    fetchFn: mockFetch
  })

  assert.equal(receivedRangeHeader, 'bytes=10-')
  assert.equal(result.fromCache, false)
  assert.equal(result.contentHash, sha256)

  const saved = await fsp.readFile(result.filePath)
  assert.deepEqual(saved, fullBytes)
})

test('model disk cache handles 416 Range Not Satisfiable by refetching from 0', async t => {
  const tempDir = await fsp.mkdtemp(path.join(os.tmpdir(), 'deskagent-cache-test-'))
  t.after(() => fsp.rm(tempDir, { recursive: true, force: true }))

  const cache = createModelDiskCache({ deskagentHome: tempDir })
  const fullBytes = Buffer.from('complete model replacement payload')
  const sha256 = crypto.createHash('sha256').update(fullBytes).digest('hex')

  // Corrupted/oversized partial file
  const partialPath = cache.getPartialPath(sha256)
  await fsp.mkdir(path.dirname(partialPath), { recursive: true })
  await fsp.writeFile(partialPath, Buffer.from('stale partial data larger than server'))

  let callCount = 0
  const mockFetch = async (url, opts) => {
    callCount++
    if (opts?.headers?.['Range']) {
      return {
        ok: false,
        status: 416,
        statusText: 'Range Not Satisfiable',
        headers: new Map(),
        text: async () => 'Requested Range Not Satisfiable'
      }
    }
    return {
      ok: true,
      status: 200,
      headers: new Map([
        ['content-type', 'model/gltf-binary'],
        ['x-content-sha256', sha256]
      ]),
      body: Readable.from(fullBytes)
    }
  }

  const result = await cache.ensureCached({
    url: '/api/companion/model/file/1/model.glb',
    contentHash: sha256,
    baseUrl: 'http://127.0.0.1:8000',
    fetchFn: mockFetch
  })

  assert.equal(callCount, 2)
  assert.equal(result.contentHash, sha256)
  const saved = await fsp.readFile(result.filePath)
  assert.deepEqual(saved, fullBytes)
})

test('model disk cache sweep evicts oldest files when cap is reached', async t => {
  const tempDir = await fsp.mkdtemp(path.join(os.tmpdir(), 'deskagent-cache-test-'))
  t.after(() => fsp.rm(tempDir, { recursive: true, force: true }))

  const cache = createModelDiskCache({ deskagentHome: tempDir })
  const cacheDir = path.join(tempDir, 'cache', 'models')
  await fsp.mkdir(cacheDir, { recursive: true })

  // Write MAX_CACHE_FILES + 5 files with sequential timestamps
  for (let i = 0; i < MAX_CACHE_FILES + 5; i++) {
    const p = path.join(cacheDir, `hash_${String(i).padStart(3, '0')}.glb`)
    await fsp.writeFile(p, `content ${i}`)
    const d = new Date(Date.now() - (MAX_CACHE_FILES + 5 - i) * 1000)
    await fsp.utimes(p, d, d)
  }

  await cache.sweep()

  const remaining = await fsp.readdir(cacheDir)
  assert.equal(remaining.length, MAX_CACHE_FILES)
  // The oldest 5 files (hash_000 to hash_004) should be deleted
  assert.equal(remaining.includes('hash_000.glb'), false)
  assert.equal(remaining.includes('hash_004.glb'), false)
  assert.equal(remaining.includes('hash_005.glb'), true)
})
