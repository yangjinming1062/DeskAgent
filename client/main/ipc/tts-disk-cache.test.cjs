const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const { cacheKey, createTtsDiskCache, MAX_CACHE_FILES, MAX_ENTRY_BYTES } = require('./tts-disk-cache.cjs')

function makeHome() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'deskagent-tts-cache-test-'))
}

function cacheDir(home, language = 'zh') {
  return path.join(home, 'audio', 'tts-cache', language)
}

const MP3 = 'audio/mpeg'

test('cacheKey is stable and separates voice from text', () => {
  assert.equal(cacheKey('冰糖', '嗯？怎么啦？'), cacheKey('冰糖', '嗯？怎么啦？'))
  assert.notEqual(cacheKey('冰糖', 'x'), cacheKey('茉莉', 'x'))
  assert.notEqual(cacheKey('冰糖', 'x'), cacheKey('冰糖', 'y'))
  // The separator has to keep the two fields from bleeding into each other.
  assert.notEqual(cacheKey('ab', 'c'), cacheKey('a', 'bc'))
  assert.match(cacheKey('冰糖', 'x'), /^[0-9a-f]{40}$/)
})

test('read returns null on miss and the written bytes on hit', async () => {
  const home = makeHome()
  const cache = createTtsDiskCache({ deskagentHome: home })
  const entry = { voice: '冰糖', text: '嗯？怎么啦？', language: 'zh' }

  assert.equal(await cache.read(entry), null)

  await cache.write({ ...entry, buffer: Buffer.from('mp3-bytes'), mimeType: MP3 })

  assert.deepEqual(await cache.read(entry), Buffer.from('mp3-bytes'))
  assert.deepEqual(fs.readdirSync(cacheDir(home)), [`${cacheKey(entry.voice, entry.text)}.mp3`])
})

test('a different voice or text is a different entry', async () => {
  const home = makeHome()
  const cache = createTtsDiskCache({ deskagentHome: home })

  await cache.write({ voice: '冰糖', text: 'hi', language: 'zh', buffer: Buffer.from('a'), mimeType: MP3 })

  assert.equal(await cache.read({ voice: '茉莉', text: 'hi', language: 'zh' }), null)
  assert.equal(await cache.read({ voice: '冰糖', text: 'hi!', language: 'zh' }), null)
  assert.equal(await cache.read({ voice: '冰糖', text: 'hi', language: 'en' }), null)
})

test('write skips non-mp3 results and oversized buffers', async () => {
  const home = makeHome()
  const cache = createTtsDiskCache({ deskagentHome: home })

  await cache.write({ voice: 'v', text: 'wav', language: 'zh', buffer: Buffer.from('a'), mimeType: 'audio/wav' })
  await cache.write({
    voice: 'v',
    text: 'huge',
    language: 'zh',
    buffer: Buffer.alloc(MAX_ENTRY_BYTES + 1),
    mimeType: MP3
  })

  assert.equal(fs.existsSync(cacheDir(home)), false)
})

test('an oversized file on disk reads as a miss', async () => {
  const home = makeHome()
  const cache = createTtsDiskCache({ deskagentHome: home })
  fs.mkdirSync(cacheDir(home), { recursive: true })
  fs.writeFileSync(path.join(cacheDir(home), `${cacheKey('v', 'bloated')}.mp3`), Buffer.alloc(MAX_ENTRY_BYTES + 1))

  assert.equal(await cache.read({ voice: 'v', text: 'bloated', language: 'zh' }), null)
})

test('write evicts the oldest entries once the cap is exceeded', async () => {
  const home = makeHome()
  const cache = createTtsDiskCache({ deskagentHome: home })
  const dir = cacheDir(home)
  fs.mkdirSync(dir, { recursive: true })

  // Seed the cap with entries stamped older than the one we are about to
  // write, so eviction order is unambiguous.
  const seeded = []
  for (let i = 0; i < MAX_CACHE_FILES; i += 1) {
    const filePath = path.join(dir, `${cacheKey('v', `seed-${i}`)}.mp3`)
    fs.writeFileSync(filePath, Buffer.from('old'))
    fs.utimesSync(filePath, new Date(1_000_000 + i * 1000), new Date(1_000_000 + i * 1000))
    seeded.push(filePath)
  }

  await cache.write({ voice: 'v', text: 'newest', language: 'zh', buffer: Buffer.from('new'), mimeType: MP3 })

  assert.equal(fs.readdirSync(dir).length, MAX_CACHE_FILES)
  assert.equal(fs.existsSync(seeded[0]), false, 'oldest entry should be evicted')
  assert.equal(fs.existsSync(seeded[1]), true)
  assert.deepEqual(await cache.read({ voice: 'v', text: 'newest', language: 'zh' }), Buffer.from('new'))
})

test('createTtsDiskCache requires deskagentHome', () => {
  assert.throws(() => createTtsDiskCache({}), /deskagentHome is required/)
})
