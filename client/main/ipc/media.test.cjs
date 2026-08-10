const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { registerMediaIpc, ttsAudioCache } = require('./media.cjs')
const { cacheKey } = require('./tts-disk-cache.cjs')

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

function setup({ stt = 'auto', tts = 'auto', sttSilentFallback = true, bridge = null, deskagentHome = null }) {
  const ipc = makeFakeIpc()
  registerMediaIpc({
    ipcMain: ipc,
    ensureBackend: async () => ({ baseUrl: 'https://backend.test', token: 'tok' }),
    deskagentHome: deskagentHome ?? fs.mkdtempSync(path.join(os.tmpdir(), 'deskagent-media-test-')),
    getRunnerBridge: () => bridge,
    getEnginePrefs: async () => ({ stt, sttSilentFallback, tts })
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
    arrayBuffer: async () => bytes ?? Buffer.from(JSON.stringify(json)),
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
  const bridge = makeBridge({
    tools: [toolSchema('speech_to_text')],
    invokeResult: { success: true, text: 'local-text' }
  })
  const ipc = setup({ stt: 'local', bridge })

  const res = await ipc.invoke('deskagent:media:stt', { dataUrl: STT_DATA_URL })

  assert.equal(res.text, 'local-text')
})

test('STT local + failure → throws, no cloud fallback', async () => {
  const bridge = makeBridge({
    tools: [toolSchema('speech_to_text')],
    invokeResult: { success: false, error: 'whisper oom' }
  })
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

test('STT auto + silent_fallback=false + local success:false → throws, no silent cloud retry', async () => {
  const bridge = makeBridge({
    tools: [toolSchema('speech_to_text')],
    invokeResult: { success: false, error: 'low confidence' }
  })
  const ipc = setup({ stt: 'auto', sttSilentFallback: false, bridge })
  global.fetch = cloudFetch({ json: { text: 'cloud-text' } })

  await assert.rejects(ipc.invoke('deskagent:media:stt', { dataUrl: STT_DATA_URL }), /low confidence/)
})

test('STT auto + silent_fallback=false + local unavailable → still falls back to cloud', async () => {
  const bridge = makeBridge({ tools: [], invokeResult: { success: true, text: 'should-not-happen' } })
  const ipc = setup({ stt: 'auto', sttSilentFallback: false, bridge })
  global.fetch = cloudFetch({ json: { text: 'cloud-text' } })

  const res = await ipc.invoke('deskagent:media:stt', { dataUrl: STT_DATA_URL })

  assert.equal(res.text, 'cloud-text')
  assert.equal(bridge.calls.length, 0)
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

test('TTS auto + cloud available + success → uses cloud, never invokes local', async () => {
  // Local engine is registered; auto should still prefer cloud so the user
  // hears the voice they picked during onboarding. Local is reserved as a
  // fallback for when the backend is unreachable.
  const bridge = makeBridge({ tools: [toolSchema('text_to_speech')], invokeResult: { success: true, path: tmpWav } })
  const ipc = setup({ tts: 'auto', bridge })
  global.fetch = cloudFetch({ bytes: Buffer.from('audio'), contentType: 'audio/mpeg' })

  const res = await ipc.invoke('deskagent:media:tts', { text: 'hi', voice: 'en_US-amy-medium' })

  assert.equal(res.mimeType, 'audio/mpeg')
  assert.ok(res.dataUrl.startsWith('data:audio/mpeg;base64,'))
  assert.equal(bridge.calls.length, 0, 'auto must not invoke local TTS when cloud succeeds')
})

test('TTS auto + cloud fails → falls back to local', async () => {
  // Cloud returned non-2xx — auto should silently fall back to local Piper.
  const bridge = makeBridge({ tools: [toolSchema('text_to_speech')], invokeResult: { success: true, path: tmpWav } })
  const ipc = setup({ tts: 'auto', bridge })
  global.fetch = cloudFetch({ status: 503, bytes: Buffer.from('upstream down'), contentType: 'text/plain' })

  const res = await ipc.invoke('deskagent:media:tts', { text: 'cloud-fails' })

  assert.equal(res.mimeType, 'audio/wav')
  assert.ok(res.dataUrl.startsWith('data:audio/wav;base64,'))
  assert.equal(bridge.calls[0].name, 'text_to_speech')
  assert.equal(bridge.calls[0].args.text, 'cloud-fails')
  // Local engine never receives the caller's voice — Piper falls back to its
  // own default (config.yaml::audio.tts.default_voice).
  assert.equal(Object.prototype.hasOwnProperty.call(bridge.calls[0].args, 'voice'), false)
})

test('TTS local invoke omits voice when empty', async () => {
  // 'local' makes the cloud path unreachable; we exercise the local caller's
  // argument shape here instead of routing through auto.
  const bridge = makeBridge({ tools: [toolSchema('text_to_speech')], invokeResult: { success: true, path: tmpWav } })
  const ipc = setup({ tts: 'local', bridge })

  await ipc.invoke('deskagent:media:tts', { text: 'omit-voice' })

  assert.equal(Object.prototype.hasOwnProperty.call(bridge.calls[0].args, 'voice'), false)
})

test('TTS local + cloud-throw irrelevant → still returns local wav', async () => {
  // Sanity: even if cloud would 503, a 'local' preference never tries cloud.
  const bridge = makeBridge({ tools: [toolSchema('text_to_speech')], invokeResult: { success: true, path: tmpWav } })
  const ipc = setup({ tts: 'local', bridge })
  global.fetch = cloudFetch({ status: 503, bytes: Buffer.from('unused') })

  const res = await ipc.invoke('deskagent:media:tts', { text: 'local-pref' })

  assert.equal(res.mimeType, 'audio/wav')
  assert.equal(bridge.calls.length, 1)
})

test('TTS cloud → always cloud', async () => {
  const bridge = makeBridge({ tools: [toolSchema('text_to_speech')], invokeResult: { success: true, path: tmpWav } })
  const ipc = setup({ tts: 'cloud', bridge })
  global.fetch = cloudFetch({ bytes: Buffer.from('audio'), contentType: 'audio/mpeg' })

  const res = await ipc.invoke('deskagent:media:tts', { text: 'always-cloud' })

  assert.equal(res.mimeType, 'audio/mpeg')
  assert.equal(bridge.calls.length, 0)
})

test('TTS local + failure → throws, no cloud fallback', async () => {
  const bridge = makeBridge({
    tools: [toolSchema('text_to_speech')],
    invokeResult: { success: false, error: 'no engine' }
  })
  const ipc = setup({ tts: 'local', bridge })
  global.fetch = cloudFetch({ bytes: Buffer.from('audio') })

  await assert.rejects(ipc.invoke('deskagent:media:tts', { text: 'local-throws' }), /no engine/)
})

test('TTS local + unavailable → throws, no cloud fallback', async () => {
  const bridge = makeBridge({ tools: [] })
  const ipc = setup({ tts: 'local', bridge })
  global.fetch = cloudFetch({ bytes: Buffer.from('audio') })

  await assert.rejects(ipc.invoke('deskagent:media:tts', { text: 'local-missing' }), /Local TTS unavailable/)
})

test('TTS rejects empty text', async () => {
  const ipc = setup({ tts: 'cloud' })
  global.fetch = cloudFetch({ bytes: Buffer.from('audio') })

  await assert.rejects(ipc.invoke('deskagent:media:tts', { text: '' }), /text is required/)
})

test('TTS back-to-back calls throttle to MIN_TTS_INTERVAL_MS apart', async () => {
  const ipc = setup({ tts: 'cloud' })
  const timestamps = []
  global.fetch = async () => {
    timestamps.push(Date.now())
    return cloudFetch({ bytes: Buffer.from('audio'), contentType: 'audio/mpeg' })()
  }

  await Promise.all([
    ipc.invoke('deskagent:media:tts', { text: 'a' }),
    ipc.invoke('deskagent:media:tts', { text: 'b' })
  ])

  assert.equal(timestamps.length, 2)
  const gap = timestamps[1] - timestamps[0]
  assert.ok(gap >= 750, `expected >=750ms gap, got ${gap}ms`)
})

// ── TTS disk cache (persist) ─────────────────────────────────────────────
//
// The memory LRU would mask every disk read, so these tests clear it between
// the two halves of a scenario to prove the bytes really came off disk.

function makeHome() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'deskagent-persist-test-'))
}

function cachedFiles(home, language = 'zh') {
  const dir = path.join(home, 'audio', 'tts-cache', language)
  return fs.existsSync(dir) ? fs.readdirSync(dir) : []
}

test('TTS persist → cloud result lands on disk and the next call skips synthesis', async () => {
  const home = makeHome()
  const ipc = setup({ tts: 'cloud', deskagentHome: home })
  let fetches = 0
  global.fetch = async (...args) => {
    fetches += 1
    return cloudFetch({ bytes: Buffer.from('mp3-bytes'), contentType: 'audio/mpeg' })(...args)
  }

  const first = await ipc.invoke('deskagent:media:tts', { text: 'persist-me', voice: '冰糖', persist: true })
  assert.equal(fetches, 1)
  assert.deepEqual(cachedFiles(home), [`${cacheKey('冰糖', 'persist-me')}.mp3`])

  ttsAudioCache.clear()
  const second = await ipc.invoke('deskagent:media:tts', { text: 'persist-me', voice: '冰糖', persist: true })

  assert.equal(fetches, 1, 'second call must be served from disk')
  assert.equal(second.dataUrl, first.dataUrl)
  assert.equal(second.mimeType, 'audio/mpeg')
})

test('TTS without persist → nothing is written to disk', async () => {
  const home = makeHome()
  const ipc = setup({ tts: 'cloud', deskagentHome: home })
  global.fetch = cloudFetch({ bytes: Buffer.from('mp3-bytes'), contentType: 'audio/mpeg' })

  await ipc.invoke('deskagent:media:tts', { text: 'ephemeral-chat-reply', voice: '冰糖' })

  assert.deepEqual(cachedFiles(home), [], 'dynamic speech must stay memory-only')
})

test('TTS persist + local engine → Piper output is not persisted', async () => {
  // A Piper wav on disk would impersonate the chosen cloud voice on every
  // later playback of this line.
  const home = makeHome()
  const bridge = makeBridge({ tools: [toolSchema('text_to_speech')], invokeResult: { success: true, path: tmpWav } })
  const ipc = setup({ tts: 'local', bridge, deskagentHome: home })

  const res = await ipc.invoke('deskagent:media:tts', { text: 'local-scripted', voice: '冰糖', persist: true })

  assert.equal(res.mimeType, 'audio/wav')
  assert.deepEqual(cachedFiles(home), [])
})

test('TTS persist → a disk hit is served even under tts.engine=local', async () => {
  // Those bytes were already paid for and carry the voice the user picked,
  // which Piper cannot reproduce. No bridge is wired, so a miss would throw.
  const home = makeHome()
  const dir = path.join(home, 'audio', 'tts-cache', 'zh')
  fs.mkdirSync(dir, { recursive: true })
  fs.writeFileSync(path.join(dir, `${cacheKey('冰糖', 'already-baked')}.mp3`), Buffer.from('disk-bytes'))

  const ipc = setup({ tts: 'local', bridge: null, deskagentHome: home })
  ttsAudioCache.clear()

  const res = await ipc.invoke('deskagent:media:tts', { text: 'already-baked', voice: '冰糖', persist: true })

  assert.equal(res.mimeType, 'audio/mpeg')
  assert.equal(res.dataUrl, `data:audio/mpeg;base64,${Buffer.from('disk-bytes').toString('base64')}`)
})

test('TTS concurrent identical calls collapse to a single synthesis', async () => {
  const ipc = setup({ tts: 'cloud', deskagentHome: makeHome() })
  let fetches = 0
  global.fetch = async (...args) => {
    fetches += 1
    return cloudFetch({ bytes: Buffer.from('mp3-bytes'), contentType: 'audio/mpeg' })(...args)
  }

  const [a, b] = await Promise.all([
    ipc.invoke('deskagent:media:tts', { text: 'spam-poke', voice: '冰糖' }),
    ipc.invoke('deskagent:media:tts', { text: 'spam-poke', voice: '冰糖' })
  ])

  assert.equal(fetches, 1, 'ten rapid pokes must not become ten cloud calls')
  assert.equal(a.dataUrl, b.dataUrl)
})
