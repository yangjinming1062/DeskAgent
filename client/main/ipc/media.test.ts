import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import { registerMediaIpc, ttsAudioCache } from './media'
import { cacheKey } from './tts-disk-cache'

const ORIG_FETCH = global.fetch

function makeFakeIpc() {
  const handlers = new Map<string, (event: any, payload: any) => any>()

  return {
    handle: (channel: string, handler: (event: any, payload: any) => any) => handlers.set(channel, handler),
    invoke: (channel: string, payload: any) => {
      const h = handlers.get(channel)

      if (!h) {
        throw new Error(`no handler for ${channel}`)
      }

      return h({}, payload)
    }
  }
}

function makeBridge({
  invokeResult = null,
  invokeThrows = null,
  tools = []
}: { invokeResult?: any; invokeThrows?: null | string; tools?: any[] } = {}) {
  const calls: any[] = []

  return {
    calls,
    getTools: () => tools,
    invoke: async (name: string, args: any) => {
      calls.push({ args, name })

      if (invokeThrows) {
        throw new Error(invokeThrows)
      }

      return invokeResult
    }
  }
}

function toolSchema(name: string) {
  return { function: { name } }
}

function setup({
  bridge = null,
  deskagentHome = null,
  stt = 'auto',
  sttSilentFallback = true,
  tts = 'auto'
}: {
  bridge?: any
  deskagentHome?: null | string
  stt?: string
  sttSilentFallback?: boolean
  tts?: string
} = {}) {
  const ipc = makeFakeIpc()
  registerMediaIpc({
    deskagentHome: deskagentHome ?? fs.mkdtempSync(path.join(os.tmpdir(), 'deskagent-media-test-')),
    ensureBackend: async () => ({ baseUrl: 'https://backend.test', token: 'tok' }),
    getEnginePrefs: async () => ({
      expiresAt: Date.now() + 10000,
      stt,
      sttEnabled: true,
      sttSilentFallback,
      tts
    }),
    getRunnerBridge: () => bridge,
    ipcMain: ipc as any
  })

  return ipc
}

function cloudFetch({ bytes = null, contentType = 'application/json', json = null, status = 200 }: any = {}) {
  return async () =>
    ({
      arrayBuffer: async () => bytes ?? Buffer.from(JSON.stringify(json)),
      headers: { get: (k: string) => (k.toLowerCase() === 'content-type' ? contentType : null) },
      ok: status >= 200 && status < 300,
      status,
      statusText: 'OK',
      text: async () => JSON.stringify(json)
    }) as any
}

const STT_DATA_URL = `data:audio/webm;base64,${Buffer.from('fake-audio').toString('base64')}`

test.after(() => {
  global.fetch = ORIG_FETCH
})

test('STT auto + local available + success → uses local, base64 passed through', async () => {
  const bridge = makeBridge({ invokeResult: { success: true, text: 'hello' }, tools: [toolSchema('speech_to_text')] })
  const ipc = setup({ bridge, stt: 'auto' })

  const res = await ipc.invoke('deskagent:media:stt', { dataUrl: STT_DATA_URL, filename: 'voice.webm' })

  assert.equal(res.text, 'hello')
  assert.equal(bridge.calls[0].name, 'speech_to_text')
  assert.equal(bridge.calls[0].args.audio_base64, Buffer.from('fake-audio').toString('base64'))
  assert.equal(bridge.calls[0].args.mime_type, 'audio/webm')
})

test('STT auto + local returns success:false → falls back to cloud', async () => {
  const bridge = makeBridge({ invokeResult: { error: 'boom', success: false }, tools: [toolSchema('speech_to_text')] })
  const ipc = setup({ bridge, stt: 'auto' })
  global.fetch = cloudFetch({ json: { text: 'cloud-text' } })

  const res = await ipc.invoke('deskagent:media:stt', { dataUrl: STT_DATA_URL })

  assert.equal(res.text, 'cloud-text')
})

test('STT auto + local throws → falls back to cloud', async () => {
  const bridge = makeBridge({ invokeThrows: 'runner down', tools: [toolSchema('speech_to_text')] })
  const ipc = setup({ bridge, stt: 'auto' })
  global.fetch = cloudFetch({ json: { text: 'cloud-text' } })

  const res = await ipc.invoke('deskagent:media:stt', { dataUrl: STT_DATA_URL })

  assert.equal(res.text, 'cloud-text')
})

test('STT auto + local not available → cloud directly (invoke never called)', async () => {
  const bridge = makeBridge({ invokeResult: { success: true, text: 'should-not-happen' }, tools: [] })
  const ipc = setup({ bridge, stt: 'auto' })
  global.fetch = cloudFetch({ json: { text: 'cloud-text' } })

  const res = await ipc.invoke('deskagent:media:stt', { dataUrl: STT_DATA_URL })

  assert.equal(res.text, 'cloud-text')
  assert.equal(bridge.calls.length, 0)
})

test('STT auto + bridge null → cloud', async () => {
  const ipc = setup({ bridge: null, stt: 'auto' })
  global.fetch = cloudFetch({ json: { text: 'cloud-text' } })

  const res = await ipc.invoke('deskagent:media:stt', { dataUrl: STT_DATA_URL })

  assert.equal(res.text, 'cloud-text')
})

test('STT cloud → always cloud even when local available', async () => {
  const bridge = makeBridge({ invokeResult: { success: true, text: 'local' }, tools: [toolSchema('speech_to_text')] })
  const ipc = setup({ bridge, stt: 'cloud' })
  global.fetch = cloudFetch({ json: { text: 'cloud-text' } })

  const res = await ipc.invoke('deskagent:media:stt', { dataUrl: STT_DATA_URL })

  assert.equal(res.text, 'cloud-text')
  assert.equal(bridge.calls.length, 0)
})

test('STT local + success → uses local', async () => {
  const bridge = makeBridge({
    invokeResult: { success: true, text: 'local-text' },
    tools: [toolSchema('speech_to_text')]
  })

  const ipc = setup({ bridge, stt: 'local' })

  const res = await ipc.invoke('deskagent:media:stt', { dataUrl: STT_DATA_URL })

  assert.equal(res.text, 'local-text')
})

test('STT local + failure → throws, no cloud fallback', async () => {
  const bridge = makeBridge({
    invokeResult: { error: 'whisper oom', success: false },
    tools: [toolSchema('speech_to_text')]
  })

  const ipc = setup({ bridge, stt: 'local' })
  global.fetch = cloudFetch({ json: { text: 'cloud-text' } })

  await assert.rejects(ipc.invoke('deskagent:media:stt', { dataUrl: STT_DATA_URL }), /whisper oom/)
})

test('STT local + unavailable → throws, no cloud fallback', async () => {
  const bridge = makeBridge({ tools: [] })
  const ipc = setup({ bridge, stt: 'local' })
  global.fetch = cloudFetch({ json: { text: 'cloud-text' } })

  await assert.rejects(ipc.invoke('deskagent:media:stt', { dataUrl: STT_DATA_URL }), /Local STT unavailable/)
})

test('STT auto + silent_fallback=false + local success:false → throws, no silent cloud retry', async () => {
  const bridge = makeBridge({
    invokeResult: { error: 'low confidence', success: false },
    tools: [toolSchema('speech_to_text')]
  })

  const ipc = setup({ bridge, stt: 'auto', sttSilentFallback: false })
  global.fetch = cloudFetch({ json: { text: 'cloud-text' } })

  await assert.rejects(ipc.invoke('deskagent:media:stt', { dataUrl: STT_DATA_URL }), /low confidence/)
})

test('STT auto + silent_fallback=false + local unavailable → still falls back to cloud', async () => {
  const bridge = makeBridge({ invokeResult: { success: true, text: 'should-not-happen' }, tools: [] })
  const ipc = setup({ bridge, stt: 'auto', sttSilentFallback: false })
  global.fetch = cloudFetch({ json: { text: 'cloud-text' } })

  const res = await ipc.invoke('deskagent:media:stt', { dataUrl: STT_DATA_URL })

  assert.equal(res.text, 'cloud-text')
  assert.equal(bridge.calls.length, 0)
})

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
  const bridge = makeBridge({ invokeResult: { path: tmpWav, success: true }, tools: [toolSchema('text_to_speech')] })
  const ipc = setup({ bridge, tts: 'auto' })
  global.fetch = cloudFetch({ bytes: Buffer.from('audio'), contentType: 'audio/mpeg' })

  const res = await ipc.invoke('deskagent:media:tts', { text: 'hi', voice: 'en_US-amy-medium' })

  assert.equal(res.mimeType, 'audio/mpeg')
  assert.ok(res.dataUrl.startsWith('data:audio/mpeg;base64,'))
  assert.equal(bridge.calls.length, 0, 'auto must not invoke local TTS when cloud succeeds')
})

test('TTS auto + cloud fails → falls back to local', async () => {
  const bridge = makeBridge({ invokeResult: { path: tmpWav, success: true }, tools: [toolSchema('text_to_speech')] })
  const ipc = setup({ bridge, tts: 'auto' })
  global.fetch = cloudFetch({ bytes: Buffer.from('upstream down'), contentType: 'text/plain', status: 503 })

  const res = await ipc.invoke('deskagent:media:tts', { text: 'cloud-fails' })

  assert.equal(res.mimeType, 'audio/wav')
  assert.ok(res.dataUrl.startsWith('data:audio/wav;base64,'))
  assert.equal(bridge.calls[0].name, 'text_to_speech')
  assert.equal(bridge.calls[0].args.text, 'cloud-fails')
  assert.equal(Object.prototype.hasOwnProperty.call(bridge.calls[0].args, 'voice'), false)
})

test('TTS local invoke omits voice when empty', async () => {
  const bridge = makeBridge({ invokeResult: { path: tmpWav, success: true }, tools: [toolSchema('text_to_speech')] })
  const ipc = setup({ bridge, tts: 'local' })

  await ipc.invoke('deskagent:media:tts', { text: 'omit-voice' })

  assert.equal(Object.prototype.hasOwnProperty.call(bridge.calls[0].args, 'voice'), false)
})

test('TTS local + cloud-throw irrelevant → still returns local wav', async () => {
  const bridge = makeBridge({ invokeResult: { path: tmpWav, success: true }, tools: [toolSchema('text_to_speech')] })
  const ipc = setup({ bridge, tts: 'local' })
  global.fetch = cloudFetch({ bytes: Buffer.from('unused'), status: 503 })

  const res = await ipc.invoke('deskagent:media:tts', { text: 'local-pref' })

  assert.equal(res.mimeType, 'audio/wav')
  assert.equal(bridge.calls.length, 1)
})

test('TTS cloud → always cloud', async () => {
  const bridge = makeBridge({ invokeResult: { path: tmpWav, success: true }, tools: [toolSchema('text_to_speech')] })
  const ipc = setup({ bridge, tts: 'cloud' })
  global.fetch = cloudFetch({ bytes: Buffer.from('audio'), contentType: 'audio/mpeg' })

  const res = await ipc.invoke('deskagent:media:tts', { text: 'always-cloud' })

  assert.equal(res.mimeType, 'audio/mpeg')
  assert.equal(bridge.calls.length, 0)
})

test('TTS local + failure → throws, no cloud fallback', async () => {
  const bridge = makeBridge({
    invokeResult: { error: 'no engine', success: false },
    tools: [toolSchema('text_to_speech')]
  })

  const ipc = setup({ bridge, tts: 'local' })
  global.fetch = cloudFetch({ bytes: Buffer.from('audio') })

  await assert.rejects(ipc.invoke('deskagent:media:tts', { text: 'local-throws' }), /no engine/)
})

test('TTS local + unavailable → throws, no cloud fallback', async () => {
  const bridge = makeBridge({ tools: [] })
  const ipc = setup({ bridge, tts: 'local' })
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
  const timestamps: number[] = []
  global.fetch = (async () => {
    timestamps.push(Date.now())

    return cloudFetch({ bytes: Buffer.from('audio'), contentType: 'audio/mpeg' })()
  }) as any

  await Promise.all([
    ipc.invoke('deskagent:media:tts', { text: 'a' }),
    ipc.invoke('deskagent:media:tts', { text: 'b' })
  ])

  assert.equal(timestamps.length, 2)
  const gap = timestamps[1] - timestamps[0]
  assert.ok(gap >= 750, `expected >=750ms gap, got ${gap}ms`)
})

function makeHome(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'deskagent-persist-test-'))
}

function cachedFiles(home: string, language = 'zh'): string[] {
  const dir = path.join(home, 'audio', 'tts-cache', language)

  return fs.existsSync(dir) ? fs.readdirSync(dir) : []
}

test('TTS persist → cloud result lands on disk and the next call skips synthesis', async () => {
  const home = makeHome()
  const ipc = setup({ deskagentHome: home, tts: 'cloud' })
  let fetches = 0
  global.fetch = (async () => {
    fetches += 1

    return cloudFetch({ bytes: Buffer.from('mp3-bytes'), contentType: 'audio/mpeg' })()
  }) as any

  const first = await ipc.invoke('deskagent:media:tts', { persist: true, text: 'persist-me', voice: '冰糖' })
  assert.equal(fetches, 1)
  assert.deepEqual(cachedFiles(home), [`${cacheKey('冰糖', 'persist-me')}.mp3`])

  ttsAudioCache.clear()
  const second = await ipc.invoke('deskagent:media:tts', { persist: true, text: 'persist-me', voice: '冰糖' })

  assert.equal(fetches, 1, 'second call must be served from disk')
  assert.equal(second.dataUrl, first.dataUrl)
  assert.equal(second.mimeType, 'audio/mpeg')
})

test('TTS without persist → nothing is written to disk', async () => {
  const home = makeHome()
  const ipc = setup({ deskagentHome: home, tts: 'cloud' })
  global.fetch = cloudFetch({ bytes: Buffer.from('mp3-bytes'), contentType: 'audio/mpeg' })

  await ipc.invoke('deskagent:media:tts', { text: 'ephemeral-chat-reply', voice: '冰糖' })

  assert.deepEqual(cachedFiles(home), [], 'dynamic speech must stay memory-only')
})

test('TTS persist + local engine → Piper output is not persisted', async () => {
  const home = makeHome()
  const bridge = makeBridge({ invokeResult: { path: tmpWav, success: true }, tools: [toolSchema('text_to_speech')] })
  const ipc = setup({ bridge, deskagentHome: home, tts: 'local' })

  const res = await ipc.invoke('deskagent:media:tts', { persist: true, text: 'local-scripted', voice: '冰糖' })

  assert.equal(res.mimeType, 'audio/wav')
  assert.deepEqual(cachedFiles(home), [])
})

test('TTS persist → a disk hit is served even under tts.engine=local', async () => {
  const home = makeHome()
  const dir = path.join(home, 'audio', 'tts-cache', 'zh')
  fs.mkdirSync(dir, { recursive: true })
  fs.writeFileSync(path.join(dir, `${cacheKey('冰糖', 'already-baked')}.mp3`), Buffer.from('disk-bytes'))

  const ipc = setup({ bridge: null, deskagentHome: home, tts: 'local' })
  ttsAudioCache.clear()

  const res = await ipc.invoke('deskagent:media:tts', { persist: true, text: 'already-baked', voice: '冰糖' })

  assert.equal(res.mimeType, 'audio/mpeg')
  assert.equal(res.dataUrl, `data:audio/mpeg;base64,${Buffer.from('disk-bytes').toString('base64')}`)
})

test('TTS concurrent identical calls collapse to a single synthesis', async () => {
  const ipc = setup({ deskagentHome: makeHome(), tts: 'cloud' })
  let fetches = 0
  global.fetch = (async () => {
    fetches += 1

    return cloudFetch({ bytes: Buffer.from('mp3-bytes'), contentType: 'audio/mpeg' })()
  }) as any

  const [a, b] = await Promise.all([
    ipc.invoke('deskagent:media:tts', { text: 'spam-poke', voice: '冰糖' }),
    ipc.invoke('deskagent:media:tts', { text: 'spam-poke', voice: '冰糖' })
  ])

  assert.equal(fetches, 1, 'ten rapid pokes must not become ten cloud calls')
  assert.equal(a.dataUrl, b.dataUrl)
})
