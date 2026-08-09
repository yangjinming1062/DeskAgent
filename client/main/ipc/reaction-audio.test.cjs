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
  assert.equal(res.tag, 'reaction.poke-light.gentle.0')
  assert.equal(res.mimeType, 'audio/mpeg')
  assert.ok(res.dataUrl.startsWith('data:audio/mpeg;base64,'))
  assert.equal(res.bytes, Buffer.from('local-bytes').length)
  fs.rmSync(tmpDir, { recursive: true, force: true })
})

test('reactionAudio:read rejects invalid tag', async () => {
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

  for (const bad of [
    '../invalid',
    'onboarding.q0',
    'reaction.foo.bar.0',
    'reaction.poke-light.gentle',
    'reaction.poke-light.gentle.x'
  ]) {
    await assert.rejects(() => ipcMain.invoke('deskagent:reactionAudio:read', bad), /invalid reaction audio tag/)
  }
  fs.rmSync(tmpDir, { recursive: true, force: true })
})

test('reactionAudio:generate fans out concurrent calls and writes one mp3 per entry', async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'deskagent-reaction-audio-gen-'))
  const ipcMain = makeFakeIpc()
  const resolveReadableFileForIpc = async filePath => ({ resolvedPath: path.resolve(filePath) })
  const mimeTypeForPath = () => 'audio/mpeg'

  // Swap ttsViaBackend for a stub that returns synthetic mp3 bytes with a
  // tiny sleep so the semaphore actually has multiple tasks in flight.
  // reaction-audio.cjs is already loaded at module top via the
  // `registerReactionAudioIpc` destructure — keep this comment so future
  // refactors don't reintroduce an unused-require lint warning.
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
      { tag: 'reaction.poke-light.gentle.0', bucket: 'poke-light', tone: 'gentle', text: '一' },
      { tag: 'reaction.poke-light.gentle.1', bucket: 'poke-light', tone: 'gentle', text: '二' },
      { tag: 'reaction.poke-medium.gentle.0', bucket: 'poke-medium', tone: 'gentle', text: '三' },
      { tag: 'reaction.drag.calm.0', bucket: 'drag', tone: 'calm', text: '四' }
    ]
    const { results } = await ipcMain.invoke('deskagent:reactionAudio:generate', {
      voice: '冰糖',
      language: 'zh',
      entries
    })

    assert.equal(calls, entries.length)
    assert.equal(results.length, entries.length)
    for (const r of results) {
      assert.equal(r.ok, true, `entry ${r.tag} should have succeeded`)
      assert.ok(r.bytes > 0)
    }

    for (const e of entries) {
      const filePath = path.join(tmpDir, 'audio', 'reactions', 'zh', `${e.tag}.mp3`)
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
      { tag: 'reaction.poke-light.gentle.0', bucket: 'poke-light', tone: 'gentle', text: 'OK' },
      { tag: 'reaction.poke-light.gentle.1', bucket: 'poke-light', tone: 'gentle', text: 'BAD' },
      { tag: 'reaction.poke-light.gentle.2', bucket: 'poke-light', tone: 'gentle', text: 'OK' }
    ]
    const { results } = await ipcMain.invoke('deskagent:reactionAudio:generate', {
      voice: '冰糖',
      language: 'zh',
      entries
    })

    assert.equal(results.length, 3)
    const byTag = Object.fromEntries(results.map(r => [r.tag, r]))
    assert.equal(byTag['reaction.poke-light.gentle.0'].ok, true)
    assert.equal(byTag['reaction.poke-light.gentle.1'].ok, false)
    assert.match(byTag['reaction.poke-light.gentle.1'].reason, /upstream 503/)
    assert.equal(byTag['reaction.poke-light.gentle.2'].ok, true)

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
      { tag: 'reaction.poke-light.gentle.0', bucket: 'poke-light', tone: 'gentle', text: 'good' },
      { tag: '../bad', bucket: 'poke-light', tone: 'gentle', text: 'bad-tag' },
      { tag: 'reaction.poke-light.gentle.1', bucket: 'unknown', tone: 'gentle', text: 'bad-bucket' },
      { tag: 'reaction.poke-light.gentle.2', bucket: 'poke-light', tone: 'mystery-tone', text: 'bad-tone' },
      { tag: 'reaction.poke-light.gentle.3', bucket: 'poke-light', tone: 'gentle', text: '' }
    ]
    const { results } = await ipcMain.invoke('deskagent:reactionAudio:generate', {
      voice: '冰糖',
      language: 'zh',
      entries
    })

    // Only the well-formed entry survives validation.
    assert.equal(results.length, 1)
    assert.equal(results[0].tag, 'reaction.poke-light.gentle.0')
    assert.equal(results[0].ok, true)
  } finally {
    mediaExports.ttsViaBackend = originalTts
    fs.rmSync(tmpDir, { recursive: true, force: true })
  }
})
