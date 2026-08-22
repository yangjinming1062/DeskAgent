import assert from 'node:assert/strict'
import crypto from 'node:crypto'
import fsp from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { Readable } from 'node:stream'
import test from 'node:test'

import { computeFileSha256, createModelDiskCache, MAX_CACHE_FILES } from './model-disk-cache'

test('computeFileSha256 calculates sha256 correctly', async t => {
  const tempDir = await fsp.mkdtemp(path.join(os.tmpdir(), 'spiritagent-cache-test-'))
  t.after(() => fsp.rm(tempDir, { force: true, recursive: true }))

  const filePath = path.join(tempDir, 'sample.bin')
  const data = Buffer.from('hello 3d companion model')
  await fsp.writeFile(filePath, data)

  const expectedSha = crypto.createHash('sha256').update(data).digest('hex')
  const actualSha = await computeFileSha256(filePath)
  assert.equal(actualSha, expectedSha)
})

test('model disk cache stores downloaded model and hits on subsequent calls', async t => {
  const tempDir = await fsp.mkdtemp(path.join(os.tmpdir(), 'spiritagent-cache-test-'))
  t.after(() => fsp.rm(tempDir, { force: true, recursive: true }))

  const cache = createModelDiskCache({ spiritagentHome: tempDir })
  const modelBytes = Buffer.from('GLB binary content 12345')
  const sha256 = crypto.createHash('sha256').update(modelBytes).digest('hex')

  let fetchCalls = 0

  const mockFetch: typeof globalThis.fetch = async () => {
    fetchCalls++

    return {
      body: Readable.from(modelBytes) as unknown as ReadableStream,
      headers: new Map<string, string>([
        ['content-type', 'model/gltf-binary'],
        ['x-content-sha256', sha256]
      ]) as unknown as Headers,
      ok: true,
      status: 200
    } as unknown as Response
  }

  const result1 = await cache.ensureCached({
    baseUrl: 'http://127.0.0.1:8000',
    contentHash: sha256,
    fetchFn: mockFetch,
    url: '/api/companion/model/file/1/model.glb'
  })

  assert.equal(result1.fromCache, false)
  assert.equal(result1.contentHash, sha256)
  assert.equal(fetchCalls, 1)
  assert.equal(await cache.has(sha256), true)

  const fileContent = await fsp.readFile(result1.filePath)
  assert.deepEqual(fileContent, modelBytes)

  const result2 = await cache.ensureCached({
    baseUrl: 'http://127.0.0.1:8000',
    contentHash: sha256,
    fetchFn: mockFetch,
    url: '/api/companion/model/file/1/model.glb'
  })

  assert.equal(result2.fromCache, true)
  assert.equal(result2.contentHash, sha256)
  assert.equal(result2.filePath, result1.filePath)
  assert.equal(fetchCalls, 1)
})

test('model disk cache supports Range resumable download', async t => {
  const tempDir = await fsp.mkdtemp(path.join(os.tmpdir(), 'spiritagent-cache-test-'))
  t.after(() => fsp.rm(tempDir, { force: true, recursive: true }))

  const cache = createModelDiskCache({ spiritagentHome: tempDir })
  const fullBytes = Buffer.from('abcdefghijklmnopqrstuvwxyz0123456789')
  const sha256 = crypto.createHash('sha256').update(fullBytes).digest('hex')

  const partialPath = cache.getPartialPath(sha256)
  await fsp.mkdir(path.dirname(partialPath), { recursive: true })
  await fsp.writeFile(partialPath, fullBytes.subarray(0, 10))

  let receivedRangeHeader: null | string = null

  const mockFetch: typeof globalThis.fetch = async (_url, init) => {
    const headers = (init?.headers ?? {}) as Record<string, string>
    receivedRangeHeader = headers['Range'] || null
    const remaining = fullBytes.subarray(10)

    return {
      body: Readable.from(remaining) as unknown as ReadableStream,
      headers: new Map<string, string>([
        ['content-type', 'model/gltf-binary'],
        ['content-range', `bytes 10-${fullBytes.length - 1}/${fullBytes.length}`],
        ['x-content-sha256', sha256]
      ]) as unknown as Headers,
      ok: true,
      status: 206
    } as unknown as Response
  }

  const result = await cache.ensureCached({
    baseUrl: 'http://127.0.0.1:8000',
    contentHash: sha256,
    fetchFn: mockFetch,
    url: '/api/companion/model/file/1/model.glb'
  })

  assert.equal(receivedRangeHeader, 'bytes=10-')
  assert.equal(result.fromCache, false)
  assert.equal(result.contentHash, sha256)

  const saved = await fsp.readFile(result.filePath)
  assert.deepEqual(saved, fullBytes)
})

test('model disk cache handles 416 Range Not Satisfiable by refetching from 0', async t => {
  const tempDir = await fsp.mkdtemp(path.join(os.tmpdir(), 'spiritagent-cache-test-'))
  t.after(() => fsp.rm(tempDir, { force: true, recursive: true }))

  const cache = createModelDiskCache({ spiritagentHome: tempDir })
  const fullBytes = Buffer.from('complete model replacement payload')
  const sha256 = crypto.createHash('sha256').update(fullBytes).digest('hex')

  const partialPath = cache.getPartialPath(sha256)
  await fsp.mkdir(path.dirname(partialPath), { recursive: true })
  await fsp.writeFile(partialPath, Buffer.from('stale partial data larger than server'))

  let callCount = 0

  const mockFetch: typeof globalThis.fetch = async (_url, init) => {
    callCount++
    const headers = (init?.headers ?? {}) as Record<string, string>

    if (headers['Range']) {
      return {
        headers: new Map<string, string>(),
        ok: false,
        status: 416,
        statusText: 'Range Not Satisfiable',
        text: async () => 'Requested Range Not Satisfiable'
      } as unknown as Response
    }

    return {
      body: Readable.from(fullBytes) as unknown as ReadableStream,
      headers: new Map<string, string>([
        ['content-type', 'model/gltf-binary'],
        ['x-content-sha256', sha256]
      ]) as unknown as Headers,
      ok: true,
      status: 200
    } as unknown as Response
  }

  const result = await cache.ensureCached({
    baseUrl: 'http://127.0.0.1:8000',
    contentHash: sha256,
    fetchFn: mockFetch,
    url: '/api/companion/model/file/1/model.glb'
  })

  assert.equal(callCount, 2)
  assert.equal(result.contentHash, sha256)
  const saved = await fsp.readFile(result.filePath)
  assert.deepEqual(saved, fullBytes)
})

test('model disk cache sweep evicts oldest files when cap is reached', async t => {
  const tempDir = await fsp.mkdtemp(path.join(os.tmpdir(), 'spiritagent-cache-test-'))
  t.after(() => fsp.rm(tempDir, { force: true, recursive: true }))

  const cache = createModelDiskCache({ spiritagentHome: tempDir })
  const cacheDir = path.join(tempDir, 'cache', 'models')
  await fsp.mkdir(cacheDir, { recursive: true })

  for (let i = 0; i < MAX_CACHE_FILES + 5; i++) {
    const p = path.join(cacheDir, `hash_${String(i).padStart(3, '0')}.glb`)
    await fsp.writeFile(p, `content ${i}`)
    const d = new Date(Date.now() - (MAX_CACHE_FILES + 5 - i) * 1000)
    await fsp.utimes(p, d, d)
  }

  await cache.sweep()

  const remaining = await fsp.readdir(cacheDir)
  assert.equal(remaining.length, MAX_CACHE_FILES)
  assert.equal(remaining.includes('hash_000.glb'), false)
  assert.equal(remaining.includes('hash_004.glb'), false)
  assert.equal(remaining.includes('hash_005.glb'), true)
})
