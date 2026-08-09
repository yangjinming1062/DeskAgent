const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const { registerReactionAudioIpc } = require('./reaction-audio.cjs')
const { sleep } = require('../shared/utils.cjs')

function makeFakeIpc() {
  const handlers = new Map()
  return {
    handle: (channel, handler) => handlers.set(channel, handler),
    invoke: (channel, ...args) => {
      const h = handlers.get(channel)
      if (!h) throw new Error(`no handler for ${channel}`)
      return h({}, ...args)
    }
  }
}

function fakeAudioDataUrl(bytes = 'fake-mp3') {
  return `data:audio/mpeg;base64,${Buffer.from(bytes).toString('base64')}`
}

test('reactionAudio:read resolves audio file from deskagentHome or dev fallback', async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'deskagent-reaction-audio-test-'))
  const ipcMain = makeFakeIpc()
  const resolveReadableFileForIpc = async filePath => ({ resolvedPath: path.resolve(filePath) })
  const mimeTypeForPath = () => 'audio/mpeg'
  const ensureBackend = async () => ({ baseUrl: 'https://backend.test', token: 'tok' })

  registerReactionAudioIpc({
    ipcMain,
    deskagentHome: tmpDir,
    mimeTypeForPath,
    hardening: { resolveReadableFileForIpc },
    ensureBackend
  })

  const localDir = path.join(tmpDir, 'audio', 'reactions', 'zh')
  fs.mkdirSync(localDir, { recursive: true })
  fs.writeFileSync(path.join(localDir, 'reaction.poke-light.gentle.0.mp3'), Buffer.from('local-bytes'))
  const res = await ipcMain.invoke('deskagent:reactionAudio:read', 'reaction.poke-light.gentle.0')
  assert.equal(res.id, 'reaction.poke-light.gentle.0')
  assert.equal(res.mimeType, 'audio/mpeg')
  assert.ok(res.dataUrl.startsWith('data:audio/mpeg;base64,'))
  assert.equal(res.bytes, Buffer.from('local-bytes').length)
  fs.rmSync(tmpDir, { recursive: true, force: true })
})

test('reactionAudio:read rejects invalid id', async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'deskagent-reaction-audio-test-'))
  const ipcMain = makeFakeIpc()
  const resolveReadableFileForIpc = async filePath => ({ resolvedPath: path.resolve(filePath) })
  const mimeTypeForPath = () => 'audio/mpeg'
  const ensureBackend = async () => ({ baseUrl: 'https://backend.test', token: 'tok' })

  registerReactionAudioIpc({
    ipcMain,
    deskagentHome: tmpDir,
    mimeTypeForPath,
    hardening: { resolveReadableFileForIpc },
    ensureBackend
  })

  for (const bad of ['../invalid', 'onboarding.q0', 'reaction/foo', 'bad:tag', '']) {
    await assert.rejects(() => ipcMain.invoke('deskagent:reactionAudio:read', bad), /invalid reaction audio id/)
  }
  fs.rmSync(tmpDir, { recursive: true, force: true })
})

test('reactionAudio:generate fans out concurrent calls and writes one mp3 per entry', async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'deskagent-reaction-audio-gen-'))
  const ipcMain = makeFakeIpc()
  const resolveReadableFileForIpc = async filePath => ({ resolvedPath: path.resolve(filePath) })
  const mimeTypeForPath = () => 'audio/mpeg'

  const mediaExports = require('./media.cjs')
  const originalTts = mediaExports.ttsViaBackend
  let concurrent = 0
  let peakConcurrent = 0
  let calls = 0
  mediaExports.ttsViaBackend = async ({ text }) => {
    calls += 1
    concurrent += 1
    peakConcurrent = Math.max(peakConcurrent, concurrent)
    await sleep(5)
    concurrent -= 1
    return { dataUrl: fakeAudioDataUrl(text), mimeType: 'audio/mpeg', voiceOut: 'test' }
  }

  try {
    registerReactionAudioIpc({
      ipcMain,
      deskagentHome: tmpDir,
      mimeTypeForPath,
      hardening: { resolveReadableFileForIpc },
      ensureBackend: async () => ({ baseUrl: 'https://backend.test', token: 'tok' })
    })

    const entries = [
      { id: 'reaction.poke-light.gentle.0', bucket: 'poke-light', tags: ['温柔'], text: '一' },
      { id: 'reaction.poke-light.gentle.1', bucket: 'poke-light', tags: ['温柔'], text: '二' },
      { id: 'reaction.poke-medium.gentle.0', bucket: 'poke-medium', tags: ['温柔'], text: '三' },
      { id: 'reaction.drag.calm.0', bucket: 'drag', tags: ['冷静'], text: '四' }
    ]
    const { results } = await ipcMain.invoke('deskagent:reactionAudio:generate', {
      voice: '冰糖',
      language: 'zh',
      entries
    })

    assert.equal(calls, entries.length)
    assert.equal(results.length, entries.length)
    for (const r of results) {
      assert.equal(r.ok, true, `entry ${r.id} should have succeeded`)
      assert.ok(r.bytes > 0)
    }

    for (const e of entries) {
      const filePath = path.join(tmpDir, 'audio', 'reactions', 'zh', `${e.id}.mp3`)
      assert.ok(fs.existsSync(filePath), `expected ${filePath} to exist on disk`)
    }
  } finally {
    mediaExports.ttsViaBackend = originalTts
    fs.rmSync(tmpDir, { recursive: true, force: true })
  }
})

test('reactionAudio:generate keeps the batch alive when individual entries fail', async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'deskagent-reaction-audio-gen-'))
  const ipcMain = makeFakeIpc()
  const resolveReadableFileForIpc = async filePath => ({ resolvedPath: path.resolve(filePath) })
  const mimeTypeForPath = () => 'audio/mpeg'

  const mediaExports = require('./media.cjs')
  const originalTts = mediaExports.ttsViaBackend
  mediaExports.ttsViaBackend = async ({ text }) => {
    if (text === 'BAD') {
      throw new Error('upstream 503')
    }
    return { dataUrl: fakeAudioDataUrl(text), mimeType: 'audio/mpeg', voiceOut: 'test' }
  }

  try {
    registerReactionAudioIpc({
      ipcMain,
      deskagentHome: tmpDir,
      mimeTypeForPath,
      hardening: { resolveReadableFileForIpc },
      ensureBackend: async () => ({ baseUrl: 'https://backend.test', token: 'tok' })
    })

    const entries = [
      { id: 'reaction.poke-light.gentle.0', bucket: 'poke-light', tags: ['温柔'], text: 'OK' },
      { id: 'reaction.poke-light.gentle.1', bucket: 'poke-light', tags: ['温柔'], text: 'BAD' },
      { id: 'reaction.poke-light.gentle.2', bucket: 'poke-light', tags: ['温柔'], text: 'OK' }
    ]
    const { results } = await ipcMain.invoke('deskagent:reactionAudio:generate', {
      voice: '冰糖',
      language: 'zh',
      entries
    })

    assert.equal(results.length, 3)
    const byId = Object.fromEntries(results.map(r => [r.id, r]))
    assert.equal(byId['reaction.poke-light.gentle.0'].ok, true)
    assert.equal(byId['reaction.poke-light.gentle.1'].ok, false)
    assert.match(byId['reaction.poke-light.gentle.1'].reason, /upstream 503/)
    assert.equal(byId['reaction.poke-light.gentle.2'].ok, true)

    assert.ok(fs.existsSync(path.join(tmpDir, 'audio', 'reactions', 'zh', 'reaction.poke-light.gentle.0.mp3')))
    assert.ok(!fs.existsSync(path.join(tmpDir, 'audio', 'reactions', 'zh', 'reaction.poke-light.gentle.1.mp3')))
    assert.ok(fs.existsSync(path.join(tmpDir, 'audio', 'reactions', 'zh', 'reaction.poke-light.gentle.2.mp3')))
  } finally {
    mediaExports.ttsViaBackend = originalTts
    fs.rmSync(tmpDir, { recursive: true, force: true })
  }
})

test('reactionAudio:generate rejects malformed entries but still processes valid ones', async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'deskagent-reaction-audio-gen-'))
  const ipcMain = makeFakeIpc()
  const resolveReadableFileForIpc = async filePath => ({ resolvedPath: path.resolve(filePath) })
  const mimeTypeForPath = () => 'audio/mpeg'

  const mediaExports = require('./media.cjs')
  const originalTts = mediaExports.ttsViaBackend
  mediaExports.ttsViaBackend = async ({ text }) => ({ dataUrl: fakeAudioDataUrl(text), mimeType: 'audio/mpeg' })

  try {
    registerReactionAudioIpc({
      ipcMain,
      deskagentHome: tmpDir,
      mimeTypeForPath,
      hardening: { resolveReadableFileForIpc },
      ensureBackend: async () => ({ baseUrl: 'https://backend.test', token: 'tok' })
    })

    const entries = [
      { id: 'reaction.poke-light.gentle.0', bucket: 'poke-light', tags: ['温柔'], text: 'good' },
      { id: '../bad', bucket: 'poke-light', tags: ['温柔'], text: 'bad-tag' },
      { id: 'reaction.poke-light.gentle.1', bucket: 'unknown', tags: ['温柔'], text: 'bad-bucket' },
      { id: 'reaction.poke-light.gentle.2', bucket: 'poke-light', tags: 'not-an-array', text: 'bad-tags' },
      { id: 'reaction.poke-light.gentle.3', bucket: 'poke-light', tags: ['温柔'], text: '' }
    ]
    const { results } = await ipcMain.invoke('deskagent:reactionAudio:generate', {
      voice: '冰糖',
      language: 'zh',
      entries
    })

    // Only the well-formed entry survives validation.
    assert.equal(results.length, 1)
    assert.equal(results[0].id, 'reaction.poke-light.gentle.0')
    assert.equal(results[0].ok, true)
  } finally {
    mediaExports.ttsViaBackend = originalTts
    fs.rmSync(tmpDir, { recursive: true, force: true })
  }
})
