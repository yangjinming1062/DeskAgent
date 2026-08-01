/**
 * Tests for main/ipc/media.cjs — STT/TTS engine routing.
 *
 * Run with: node --test main/ipc/media.test.cjs
 *
 * The routing logic is exercised by injecting a fake runner bridge (tool list +
 * invoke results) and a fake engine-preference resolver. The cloud path is
 * reached via a mocked global fetch; the local TTS path reads a real temp WAV
 * so fs.readFileSync is covered without monkeypatching.
 */

const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { registerMediaIpc } = require('./media.cjs')

const ORIG_FETCH = global.fetch

function makeFakeIpc() {
  const handlers = new Map()
  return {
    handle: (channel, handler) => handlers.set(channel, handler),
    invoke: (channel, payload) => {
      const h = handlers.get(channel)
      if (!h) throw new Error(`no handler for ${channel}`)
      return h({}, payload)
    }
  }
}

// Bridge mock that records invoke() calls so tests can assert arg passthrough.
function makeBridge({ tools = [], invokeResult = null, invokeThrows = null }) {
  const calls = []
  return {
    getTools: () => tools,
    invoke: async (name, args) => {
      calls.push({ name, args })
      if (invokeThrows) throw new Error(invokeThrows)
      return invokeResult
    },
    calls
  }
}

function toolSchema(name) {
  return { function: { name } }
}

function setup({ stt = 'auto', tts = 'auto', bridge = null }) {
  const ipc = makeFakeIpc()
  registerMediaIpc({
    ipcMain: ipc,
    ensureBackend: async () => ({ baseUrl: 'https://backend.test', token: 'tok' }),
    getRunnerBridge: () => bridge,
    getEnginePrefs: async () => ({ stt, tts })
  })
  return ipc
}

// Minimal fetch double for the cloud path.
function cloudFetch({ json = null, bytes = null, contentType = 'application/json', status = 200 }) {
  return async () => ({
    ok: status >= 200 && status < 300,
    status,
    statusText: 'OK',
    headers: { get: k => (k.toLowerCase() === 'content-type' ? contentType : null) },
    arrayBuffer: async () => (bytes ?? Buffer.from(JSON.stringify(json))),
    text: async () => JSON.stringify(json)
  })
}

const STT_DATA_URL = `data:audio/webm;base64,${Buffer.from('fake-audio').toString('base64')}`

test.after(() => {
  global.fetch = ORIG_FETCH
})

// ── STT ──────────────────────────────────────────────────────────────────

test('STT auto + local available + success → uses local, base64 passed through', async () => {
  const bridge = makeBridge({ tools: [toolSchema('speech_to_text')], invokeResult: { success: true, text: 'hello' } })
  const ipc = setup({ stt: 'auto', bridge })

  const res = await ipc.invoke('deskagent:media:stt', { dataUrl: STT_DATA_URL, filename: 'voice.webm' })

  assert.equal(res.text, 'hello')
  assert.equal(bridge.calls[0].name, 'speech_to_text')
  assert.equal(bridge.calls[0].args.audio_base64, Buffer.from('fake-audio').toString('base64'))
  assert.equal(bridge.calls[0].args.mime_type, 'audio/webm')
})

test('STT auto + local returns success:false → falls back to cloud', async () => {
  const bridge = makeBridge({ tools: [toolSchema('speech_to_text')], invokeResult: { success: false, error: 'boom' } })
  const ipc = setup({ stt: 'auto', bridge })
  global.fetch = cloudFetch({ json: { text: 'cloud-text' } })

  const res = await ipc.invoke('deskagent:media:stt', { dataUrl: STT_DATA_URL })

  assert.equal(res.text, 'cloud-text')
})

test('STT auto + local throws → falls back to cloud', async () => {
  const bridge = makeBridge({ tools: [toolSchema('speech_to_text')], invokeThrows: 'runner down' })
  const ipc = setup({ stt: 'auto', bridge })
  global.fetch = cloudFetch({ json: { text: 'cloud-text' } })

  const res = await ipc.invoke('deskagent:media:stt', { dataUrl: STT_DATA_URL })

  assert.equal(res.text, 'cloud-text')
})

test('STT auto + local not available → cloud directly (invoke never called)', async () => {
  const bridge = makeBridge({ tools: [], invokeResult: { success: true, text: 'should-not-happen' } })
  const ipc = setup({ stt: 'auto', bridge })
  global.fetch = cloudFetch({ json: { text: 'cloud-text' } })

  const res = await ipc.invoke('deskagent:media:stt', { dataUrl: STT_DATA_URL })

  assert.equal(res.text, 'cloud-text')
  assert.equal(bridge.calls.length, 0)
})

test('STT auto + bridge null → cloud', async () => {
  const ipc = setup({ stt: 'auto', bridge: null })
  global.fetch = cloudFetch({ json: { text: 'cloud-text' } })

  const res = await ipc.invoke('deskagent:media:stt', { dataUrl: STT_DATA_URL })

  assert.equal(res.text, 'cloud-text')
})

test('STT cloud → always cloud even when local available', async () => {
  const bridge = makeBridge({ tools: [toolSchema('speech_to_text')], invokeResult: { success: true, text: 'local' } })
  const ipc = setup({ stt: 'cloud', bridge })
  global.fetch = cloudFetch({ json: { text: 'cloud-text' } })

  const res = await ipc.invoke('deskagent:media:stt', { dataUrl: STT_DATA_URL })

  assert.equal(res.text, 'cloud-text')
  assert.equal(bridge.calls.length, 0)
})

test('STT local + success → uses local', async () => {
  const bridge = makeBridge({ tools: [toolSchema('speech_to_text')], invokeResult: { success: true, text: 'local-text' } })
  const ipc = setup({ stt: 'local', bridge })

  const res = await ipc.invoke('deskagent:media:stt', { dataUrl: STT_DATA_URL })

  assert.equal(res.text, 'local-text')
})

test('STT local + failure → throws, no cloud fallback', async () => {
  const bridge = makeBridge({ tools: [toolSchema('speech_to_text')], invokeResult: { success: false, error: 'whisper oom' } })
  const ipc = setup({ stt: 'local', bridge })
  global.fetch = cloudFetch({ json: { text: 'cloud-text' } })

  await assert.rejects(ipc.invoke('deskagent:media:stt', { dataUrl: STT_DATA_URL }), /whisper oom/)
})

test('STT local + unavailable → throws, no cloud fallback', async () => {
  const bridge = makeBridge({ tools: [] })
  const ipc = setup({ stt: 'local', bridge })
  global.fetch = cloudFetch({ json: { text: 'cloud-text' } })

  await assert.rejects(ipc.invoke('deskagent:media:stt', { dataUrl: STT_DATA_URL }), /Local STT unavailable/)
})

// ── TTS ──────────────────────────────────────────────────────────────────

let tmpWav = ''

test.before(() => {
  tmpWav = path.join(os.tmpdir(), `media-test-${Date.now()}.wav`)
  fs.writeFileSync(tmpWav, Buffer.from('RIFF...fake-wav-bytes'))
})
test.after(() => {
  try {
    fs.unlinkSync(tmpWav)
  } catch {
    /* already gone */
  }
})

test('TTS auto + local available + success → reads WAV, returns audio/wav dataUrl', async () => {
  const bridge = makeBridge({ tools: [toolSchema('text_to_speech')], invokeResult: { success: true, path: tmpWav } })
  const ipc = setup({ tts: 'auto', bridge })

  const res = await ipc.invoke('deskagent:media:tts', { text: 'hi', voice: 'en_US-amy-medium' })

  assert.equal(res.mimeType, 'audio/wav')
  assert.ok(res.dataUrl.startsWith('data:audio/wav;base64,'))
  assert.equal(bridge.calls[0].name, 'text_to_speech')
  assert.equal(bridge.calls[0].args.text, 'hi')
  // Local engine never receives the caller's voice — Piper falls back to its
  // own default (config.yaml::audio.tts.default_voice). See media.cjs:79-81.
  assert.equal(Object.prototype.hasOwnProperty.call(bridge.calls[0].args, 'voice'), false)
})

test('TTS local invoke omits voice when empty', async () => {
  const bridge = makeBridge({ tools: [toolSchema('text_to_speech')], invokeResult: { success: true, path: tmpWav } })
  const ipc = setup({ tts: 'auto', bridge })

  await ipc.invoke('deskagent:media:tts', { text: 'hi' })

  assert.equal(Object.prototype.hasOwnProperty.call(bridge.calls[0].args, 'voice'), false)
})

test('TTS auto + local fails → falls back to cloud', async () => {
  const bridge = makeBridge({ tools: [toolSchema('text_to_speech')], invokeResult: { success: false, error: 'no piper voice' } })
  const ipc = setup({ tts: 'auto', bridge })
  global.fetch = cloudFetch({ bytes: Buffer.from('audio'), contentType: 'audio/mpeg' })

  const res = await ipc.invoke('deskagent:media:tts', { text: 'hi' })

  assert.equal(res.mimeType, 'audio/mpeg')
})

test('TTS cloud → always cloud', async () => {
  const bridge = makeBridge({ tools: [toolSchema('text_to_speech')], invokeResult: { success: true, path: tmpWav } })
  const ipc = setup({ tts: 'cloud', bridge })
  global.fetch = cloudFetch({ bytes: Buffer.from('audio'), contentType: 'audio/mpeg' })

  const res = await ipc.invoke('deskagent:media:tts', { text: 'hi' })

  assert.equal(res.mimeType, 'audio/mpeg')
  assert.equal(bridge.calls.length, 0)
})

test('TTS local + failure → throws, no cloud fallback', async () => {
  const bridge = makeBridge({ tools: [toolSchema('text_to_speech')], invokeResult: { success: false, error: 'no engine' } })
  const ipc = setup({ tts: 'local', bridge })
  global.fetch = cloudFetch({ bytes: Buffer.from('audio') })

  await assert.rejects(ipc.invoke('deskagent:media:tts', { text: 'hi' }), /no engine/)
})

test('TTS local + unavailable → throws, no cloud fallback', async () => {
  const bridge = makeBridge({ tools: [] })
  const ipc = setup({ tts: 'local', bridge })
  global.fetch = cloudFetch({ bytes: Buffer.from('audio') })

  await assert.rejects(ipc.invoke('deskagent:media:tts', { text: 'hi' }), /Local TTS unavailable/)
})

test('TTS rejects empty text', async () => {
  const ipc = setup({ tts: 'cloud' })
  global.fetch = cloudFetch({ bytes: Buffer.from('audio') })

  await assert.rejects(ipc.invoke('deskagent:media:tts', { text: '' }), /text is required/)
})
