import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import type { IpcMain, IpcMainInvokeEvent } from 'electron'

import type { MediaIpcDeps } from './media'
import { type EngineMode, registerMediaIpc, ttsAudioCache } from './media'
import { cacheKey } from './tts-disk-cache'

type FakeBridge = NonNullable<ReturnType<NonNullable<MediaIpcDeps['getRunnerBridge']>>>
type InvokeArgs = Parameters<NonNullable<FakeBridge['invoke']>>[1]
type BridgeTools = ReturnType<NonNullable<FakeBridge['getTools']>>

interface BridgeSpy {
  calls: Array<{ args: InvokeArgs; name: string }>
  getTools: () => BridgeTools
  invoke: (name: string, args: InvokeArgs) => Promise<unknown>
}

const ORIG_FETCH = global.fetch

type IpcHandler = (event: IpcMainInvokeEvent, payload: unknown) => unknown

interface FakeIpc {
  handle: (channel: string, handler: IpcHandler) => void
  invoke: (channel: string, payload?: unknown) => Promise<unknown>
}

function makeFakeIpc(): FakeIpc {
  const handlers = new Map<string, IpcHandler>()

  return {
    handle: (channel, handler) => {
      handlers.set(channel, handler)
    },
    invoke: async (channel, payload) => {
      const h = handlers.get(channel)

      if (!h) {
        throw new Error(`no handler for ${channel}`)
      }

      return h({} as IpcMainInvokeEvent, payload) as Promise<unknown>
    }
  }
}

function makeBridge({
  invokeResult = null,
  invokeThrows = null,
  tools = []
}: { invokeResult?: unknown; invokeThrows?: null | string; tools?: BridgeTools } = {}): BridgeSpy {
  const calls: Array<{ args: InvokeArgs; name: string }> = []

  return {
    calls,
    getTools: () => tools,
    invoke: async (name: string, args: InvokeArgs) => {
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
  minTtsIntervalMs,
  spiritagentHome = null,
  stt = 'auto' as EngineMode,
  sttBurst,
  sttMaxConcurrency,
  sttRefillRate,
  sttSilentFallback = true,
  tts = 'auto' as EngineMode,
  ttsMaxQueueSize
}: {
  bridge?: BridgeSpy | null
  minTtsIntervalMs?: number
  spiritagentHome?: null | string
  stt?: EngineMode
  sttBurst?: number
  sttMaxConcurrency?: number
  sttRefillRate?: number
  sttSilentFallback?: boolean
  tts?: EngineMode
  ttsMaxQueueSize?: number
} = {}) {
  const ipc = makeFakeIpc()
  registerMediaIpc({
    spiritagentHome: spiritagentHome ?? fs.mkdtempSync(path.join(os.tmpdir(), 'spiritagent-media-test-')),
    ensureBackend: async () => ({ baseUrl: 'https://backend.test', token: 'tok' }),
    getEnginePrefs: async () => ({
      expiresAt: Date.now() + 10000,
      stt,
      sttEnabled: true,
      sttSilentFallback,
      tts
    }),
    getRunnerBridge: () => bridge as unknown as FakeBridge,
    ipcMain: ipc as unknown as IpcMain,
    minTtsIntervalMs,
    sttBurst,
    sttMaxConcurrency,
    sttRefillRate,
    ttsMaxQueueSize
  })

  return ipc
}

interface FakeResponse {
  arrayBuffer: () => Promise<ArrayBufferLike>
  headers: { get: (k: string) => null | string }
  ok: boolean
  status: number
  statusText: string
  text: () => Promise<string>
}

interface CloudFetchOptions {
  bytes?: Buffer | null
  contentType?: string
  json?: unknown
  status?: number
}

function makeFakeResponse({
  bytes = null,
  contentType = 'application/json',
  json = null,
  status = 200
}: CloudFetchOptions): FakeResponse {
  const data = bytes ?? Buffer.from(JSON.stringify(json))
  const arrayBuf = data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength)

  return {
    arrayBuffer: async () => arrayBuf,
    headers: { get: (k: string) => (k.toLowerCase() === 'content-type' ? contentType : null) },
    ok: status >= 200 && status < 300,
    status,
    statusText: 'OK',
    text: async () => JSON.stringify(json)
  }
}

function cloudFetch(options: CloudFetchOptions = {}): typeof globalThis.fetch {
  return (async () => makeFakeResponse(options)) as unknown as typeof globalThis.fetch
}

const STT_DATA_URL = `data:audio/webm;base64,${Buffer.from('fake-audio').toString('base64')}`

test.after(() => {
  global.fetch = ORIG_FETCH
})

test('STT auto + local available + success → uses local, base64 passed through', async () => {
  const bridge = makeBridge({ invokeResult: { success: true, text: 'hello' }, tools: [toolSchema('speech_to_text')] })
  const ipc = setup({ bridge, stt: 'auto' })

  const res = await ipc.invoke('spiritagent:media:stt', { dataUrl: STT_DATA_URL, filename: 'voice.webm' })

  assert.equal((res as { text: string }).text, 'hello')
  assert.equal(bridge.calls[0].name, 'speech_to_text')
  assert.equal(
    (bridge.calls[0].args as { audio_base64: string }).audio_base64,
    Buffer.from('fake-audio').toString('base64')
  )
  assert.equal((bridge.calls[0].args as { mime_type: string }).mime_type, 'audio/webm')
})

test('STT auto + local returns success:false → falls back to cloud', async () => {
  const bridge = makeBridge({ invokeResult: { error: 'boom', success: false }, tools: [toolSchema('speech_to_text')] })
  const ipc = setup({ bridge, stt: 'auto' })
  global.fetch = cloudFetch({ json: { text: 'cloud-text' } })

  const res = await ipc.invoke('spiritagent:media:stt', { dataUrl: STT_DATA_URL })

  assert.equal((res as { text: string }).text, 'cloud-text')
})

test('STT auto + local throws → falls back to cloud', async () => {
  const bridge = makeBridge({ invokeThrows: 'runner down', tools: [toolSchema('speech_to_text')] })
  const ipc = setup({ bridge, stt: 'auto' })
  global.fetch = cloudFetch({ json: { text: 'cloud-text' } })

  const res = await ipc.invoke('spiritagent:media:stt', { dataUrl: STT_DATA_URL })

  assert.equal((res as { text: string }).text, 'cloud-text')
})

test('STT auto + local not available → cloud directly (invoke never called)', async () => {
  const bridge = makeBridge({ invokeResult: { success: true, text: 'should-not-happen' }, tools: [] })
  const ipc = setup({ bridge, stt: 'auto' })
  global.fetch = cloudFetch({ json: { text: 'cloud-text' } })

  const res = await ipc.invoke('spiritagent:media:stt', { dataUrl: STT_DATA_URL })

  assert.equal((res as { text: string }).text, 'cloud-text')
  assert.equal(bridge.calls.length, 0)
})

test('STT auto + bridge null → cloud', async () => {
  const ipc = setup({ bridge: null, stt: 'auto' })
  global.fetch = cloudFetch({ json: { text: 'cloud-text' } })

  const res = await ipc.invoke('spiritagent:media:stt', { dataUrl: STT_DATA_URL })

  assert.equal((res as { text: string }).text, 'cloud-text')
})

test('STT cloud → always cloud even when local available', async () => {
  const bridge = makeBridge({ invokeResult: { success: true, text: 'local' }, tools: [toolSchema('speech_to_text')] })
  const ipc = setup({ bridge, stt: 'cloud' })
  global.fetch = cloudFetch({ json: { text: 'cloud-text' } })

  const res = await ipc.invoke('spiritagent:media:stt', { dataUrl: STT_DATA_URL })

  assert.equal((res as { text: string }).text, 'cloud-text')
  assert.equal(bridge.calls.length, 0)
})

test('STT local + success → uses local', async () => {
  const bridge = makeBridge({
    invokeResult: { success: true, text: 'local-text' },
    tools: [toolSchema('speech_to_text')]
  })

  const ipc = setup({ bridge, stt: 'local' })

  const res = await ipc.invoke('spiritagent:media:stt', { dataUrl: STT_DATA_URL })

  assert.equal((res as { text: string }).text, 'local-text')
})

test('STT local + failure → throws, no cloud fallback', async () => {
  const bridge = makeBridge({
    invokeResult: { error: 'whisper oom', success: false },
    tools: [toolSchema('speech_to_text')]
  })

  const ipc = setup({ bridge, stt: 'local' })
  global.fetch = cloudFetch({ json: { text: 'cloud-text' } })

  await assert.rejects(ipc.invoke('spiritagent:media:stt', { dataUrl: STT_DATA_URL }), /whisper oom/)
})

test('STT local + unavailable → throws, no cloud fallback', async () => {
  const bridge = makeBridge({ tools: [] })
  const ipc = setup({ bridge, stt: 'local' })
  global.fetch = cloudFetch({ json: { text: 'cloud-text' } })

  await assert.rejects(ipc.invoke('spiritagent:media:stt', { dataUrl: STT_DATA_URL }), /Local STT unavailable/)
})

test('STT auto + silent_fallback=false + local success:false → throws, no silent cloud retry', async () => {
  const bridge = makeBridge({
    invokeResult: { error: 'low confidence', success: false },
    tools: [toolSchema('speech_to_text')]
  })

  const ipc = setup({ bridge, stt: 'auto', sttSilentFallback: false })
  global.fetch = cloudFetch({ json: { text: 'cloud-text' } })

  await assert.rejects(ipc.invoke('spiritagent:media:stt', { dataUrl: STT_DATA_URL }), /low confidence/)
})

test('STT auto + silent_fallback=false + local unavailable → still falls back to cloud', async () => {
  const bridge = makeBridge({ invokeResult: { success: true, text: 'should-not-happen' }, tools: [] })
  const ipc = setup({ bridge, stt: 'auto', sttSilentFallback: false })
  global.fetch = cloudFetch({ json: { text: 'cloud-text' } })

  const res = await ipc.invoke('spiritagent:media:stt', { dataUrl: STT_DATA_URL })

  assert.equal((res as { text: string }).text, 'cloud-text')
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

  const res = await ipc.invoke('spiritagent:media:tts', { text: 'hi', voice: 'en_US-amy-medium' })

  assert.equal((res as { mimeType: string }).mimeType, 'audio/mpeg')
  assert.ok((res as { dataUrl: string }).dataUrl.startsWith('data:audio/mpeg;base64,'))
  assert.equal(bridge.calls.length, 0, 'auto must not invoke local TTS when cloud succeeds')
})

test('TTS auto + cloud fails → falls back to local', async () => {
  const bridge = makeBridge({ invokeResult: { path: tmpWav, success: true }, tools: [toolSchema('text_to_speech')] })
  const ipc = setup({ bridge, tts: 'auto' })
  global.fetch = cloudFetch({ bytes: Buffer.from('upstream down'), contentType: 'text/plain', status: 503 })

  const res = await ipc.invoke('spiritagent:media:tts', { text: 'cloud-fails' })

  assert.equal((res as { mimeType: string }).mimeType, 'audio/wav')
  assert.ok((res as { dataUrl: string }).dataUrl.startsWith('data:audio/wav;base64,'))
  assert.equal(bridge.calls[0].name, 'text_to_speech')
  assert.equal((bridge.calls[0].args as { text: string }).text, 'cloud-fails')
  assert.equal(Object.prototype.hasOwnProperty.call(bridge.calls[0].args, 'voice'), false)
})

test('TTS local invoke omits voice when empty', async () => {
  const bridge = makeBridge({ invokeResult: { path: tmpWav, success: true }, tools: [toolSchema('text_to_speech')] })
  const ipc = setup({ bridge, tts: 'local' })

  await ipc.invoke('spiritagent:media:tts', { text: 'omit-voice' })

  assert.equal(Object.prototype.hasOwnProperty.call(bridge.calls[0].args, 'voice'), false)
})

test('TTS local + cloud-throw irrelevant → still returns local wav', async () => {
  const bridge = makeBridge({ invokeResult: { path: tmpWav, success: true }, tools: [toolSchema('text_to_speech')] })
  const ipc = setup({ bridge, tts: 'local' })
  global.fetch = cloudFetch({ bytes: Buffer.from('unused'), status: 503 })

  const res = await ipc.invoke('spiritagent:media:tts', { text: 'local-pref' })

  assert.equal((res as { mimeType: string }).mimeType, 'audio/wav')
  assert.equal(bridge.calls.length, 1)
})

test('TTS cloud → always cloud', async () => {
  const bridge = makeBridge({ invokeResult: { path: tmpWav, success: true }, tools: [toolSchema('text_to_speech')] })
  const ipc = setup({ bridge, tts: 'cloud' })
  global.fetch = cloudFetch({ bytes: Buffer.from('audio'), contentType: 'audio/mpeg' })

  const res = await ipc.invoke('spiritagent:media:tts', { text: 'always-cloud' })

  assert.equal((res as { mimeType: string }).mimeType, 'audio/mpeg')
  assert.equal(bridge.calls.length, 0)
})

test('TTS local + failure → throws, no cloud fallback', async () => {
  const bridge = makeBridge({
    invokeResult: { error: 'no engine', success: false },
    tools: [toolSchema('text_to_speech')]
  })

  const ipc = setup({ bridge, tts: 'local' })
  global.fetch = cloudFetch({ bytes: Buffer.from('audio') })

  await assert.rejects(ipc.invoke('spiritagent:media:tts', { text: 'local-throws' }), /no engine/)
})

test('TTS local + unavailable → throws, no cloud fallback', async () => {
  const bridge = makeBridge({ tools: [] })
  const ipc = setup({ bridge, tts: 'local' })
  global.fetch = cloudFetch({ bytes: Buffer.from('audio') })

  await assert.rejects(ipc.invoke('spiritagent:media:tts', { text: 'local-missing' }), /Local TTS unavailable/)
})

test('TTS rejects empty text', async () => {
  const ipc = setup({ tts: 'cloud' })
  global.fetch = cloudFetch({ bytes: Buffer.from('audio') })

  await assert.rejects(ipc.invoke('spiritagent:media:tts', { text: '' }), /text is required/)
})

test('TTS back-to-back calls throttle to MIN_TTS_INTERVAL_MS apart', async () => {
  const ipc = setup({ tts: 'cloud' })
  const timestamps: number[] = []
  global.fetch = (async () => {
    timestamps.push(Date.now())

    return makeFakeResponse({ bytes: Buffer.from('audio'), contentType: 'audio/mpeg' })
  }) as unknown as typeof globalThis.fetch

  await Promise.all([
    ipc.invoke('spiritagent:media:tts', { text: 'a' }),
    ipc.invoke('spiritagent:media:tts', { text: 'b' })
  ])

  assert.equal(timestamps.length, 2)
  const gap = timestamps[1] - timestamps[0]
  assert.ok(gap >= 750, `expected >=750ms gap, got ${gap}ms`)
})

function makeHome(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'spiritagent-persist-test-'))
}

function cachedFiles(home: string, language = 'zh'): string[] {
  const dir = path.join(home, 'audio', 'tts-cache', language)

  return fs.existsSync(dir) ? fs.readdirSync(dir) : []
}

test('TTS persist → cloud result lands on disk and the next call skips synthesis', async () => {
  const home = makeHome()
  const ipc = setup({ spiritagentHome: home, tts: 'cloud' })
  let fetches = 0
  global.fetch = (async () => {
    fetches += 1

    return makeFakeResponse({ bytes: Buffer.from('mp3-bytes'), contentType: 'audio/mpeg' })
  }) as unknown as typeof globalThis.fetch

  const first = await ipc.invoke('spiritagent:media:tts', { persist: true, text: 'persist-me', voice: '冰糖' })
  assert.equal(fetches, 1)
  assert.deepEqual(cachedFiles(home), [`${cacheKey('冰糖', 'persist-me')}.mp3`])

  ttsAudioCache.clear()
  const second = await ipc.invoke('spiritagent:media:tts', { persist: true, text: 'persist-me', voice: '冰糖' })

  assert.equal(fetches, 1, 'second call must be served from disk')
  assert.equal((second as { dataUrl: string }).dataUrl, (first as { dataUrl: string }).dataUrl)
  assert.equal((second as { mimeType: string }).mimeType, 'audio/mpeg')
})

test('TTS without persist → nothing is written to disk', async () => {
  const home = makeHome()
  const ipc = setup({ spiritagentHome: home, tts: 'cloud' })
  global.fetch = cloudFetch({ bytes: Buffer.from('mp3-bytes'), contentType: 'audio/mpeg' })

  await ipc.invoke('spiritagent:media:tts', { text: 'ephemeral-chat-reply', voice: '冰糖' })

  assert.deepEqual(cachedFiles(home), [], 'dynamic speech must stay memory-only')
})

test('TTS persist + local engine → Piper output is not persisted', async () => {
  const home = makeHome()
  const bridge = makeBridge({ invokeResult: { path: tmpWav, success: true }, tools: [toolSchema('text_to_speech')] })
  const ipc = setup({ bridge, spiritagentHome: home, tts: 'local' })

  const res = await ipc.invoke('spiritagent:media:tts', { persist: true, text: 'local-scripted', voice: '冰糖' })

  assert.equal((res as { mimeType: string }).mimeType, 'audio/wav')
  assert.deepEqual(cachedFiles(home), [])
})

test('TTS persist → a disk hit is served even under tts.engine=local', async () => {
  const home = makeHome()
  const dir = path.join(home, 'audio', 'tts-cache', 'zh')
  fs.mkdirSync(dir, { recursive: true })
  fs.writeFileSync(path.join(dir, `${cacheKey('冰糖', 'already-baked')}.mp3`), Buffer.from('disk-bytes'))

  const ipc = setup({ bridge: null, spiritagentHome: home, tts: 'local' })
  ttsAudioCache.clear()

  const res = await ipc.invoke('spiritagent:media:tts', { persist: true, text: 'already-baked', voice: '冰糖' })

  assert.equal((res as { mimeType: string }).mimeType, 'audio/mpeg')
  assert.equal(
    (res as { dataUrl: string }).dataUrl,
    `data:audio/mpeg;base64,${Buffer.from('disk-bytes').toString('base64')}`
  )
})

test('TTS concurrent identical calls collapse to a single synthesis', async () => {
  const ipc = setup({ spiritagentHome: makeHome(), tts: 'cloud' })
  let fetches = 0
  global.fetch = (async () => {
    fetches += 1

    return makeFakeResponse({ bytes: Buffer.from('mp3-bytes'), contentType: 'audio/mpeg' })
  }) as unknown as typeof globalThis.fetch

  const [a, b] = await Promise.all([
    ipc.invoke('spiritagent:media:tts', { text: 'spam-poke', voice: '冰糖' }),
    ipc.invoke('spiritagent:media:tts', { text: 'spam-poke', voice: '冰糖' })
  ])

  assert.equal(fetches, 1, 'ten rapid pokes must not become ten cloud calls')
  assert.equal((a as { dataUrl: string }).dataUrl, (b as { dataUrl: string }).dataUrl)
})

test('STT concurrent calls exceeding maxConcurrency fail fast with busy error', async () => {
  let activeInvokes = 0
  let maxActiveObserved = 0

  const bridge: BridgeSpy = {
    calls: [],
    getTools: () => [toolSchema('speech_to_text')],
    invoke: async () => {
      activeInvokes += 1
      maxActiveObserved = Math.max(maxActiveObserved, activeInvokes)
      await new Promise(r => setTimeout(r, 60))
      activeInvokes -= 1

      return { success: true, text: 'transcribed' }
    }
  }

  const ipc = setup({ bridge, stt: 'local', sttMaxConcurrency: 2 })

  const results = await Promise.allSettled([
    ipc.invoke('spiritagent:media:stt', { dataUrl: STT_DATA_URL }),
    ipc.invoke('spiritagent:media:stt', { dataUrl: STT_DATA_URL }),
    ipc.invoke('spiritagent:media:stt', { dataUrl: STT_DATA_URL }),
    ipc.invoke('spiritagent:media:stt', { dataUrl: STT_DATA_URL })
  ])

  const fulfilled = results.filter(r => r.status === 'fulfilled')
  const rejected = results.filter(r => r.status === 'rejected')

  assert.equal(fulfilled.length, 2, 'only 2 concurrent tasks should execute')
  assert.equal(rejected.length, 2, 'excess tasks should be rejected immediately')
  assert.equal(maxActiveObserved, 2, 'runner should never observe >2 concurrent STT calls')

  for (const r of rejected) {
    assert.match((r as PromiseRejectedResult).reason.message, /STT is busy: maximum concurrency reached/)
  }

  const subsequent = await ipc.invoke('spiritagent:media:stt', { dataUrl: STT_DATA_URL })
  assert.equal((subsequent as { text: string }).text, 'transcribed')
})

test('STT burst exceeding token bucket fails fast with rate limit error', async () => {
  const bridge = makeBridge({ invokeResult: { success: true, text: 'ok' }, tools: [toolSchema('speech_to_text')] })
  const ipc = setup({ bridge, stt: 'local', sttBurst: 2, sttMaxConcurrency: 10, sttRefillRate: 0 })

  await ipc.invoke('spiritagent:media:stt', { dataUrl: STT_DATA_URL })
  await ipc.invoke('spiritagent:media:stt', { dataUrl: STT_DATA_URL })

  await assert.rejects(
    ipc.invoke('spiritagent:media:stt', { dataUrl: STT_DATA_URL }),
    /STT is busy: rate limit exceeded/
  )
})

test('TTS queue full immediately returns busy error without appending to promise chain', async () => {
  let finishRunning: () => void = () => {}

  const runningPromise = new Promise<void>(resolve => {
    finishRunning = resolve
  })

  global.fetch = (async () => {
    await runningPromise

    return makeFakeResponse({ bytes: Buffer.from('audio'), contentType: 'audio/mpeg' })
  }) as unknown as typeof globalThis.fetch

  const ipc = setup({ minTtsIntervalMs: 0, tts: 'cloud', ttsMaxQueueSize: 2 })

  const task1 = ipc.invoke('spiritagent:media:tts', { text: 'item-1' })
  const task2 = ipc.invoke('spiritagent:media:tts', { text: 'item-2' })
  const task3 = ipc.invoke('spiritagent:media:tts', { text: 'item-3' })

  await assert.rejects(ipc.invoke('spiritagent:media:tts', { text: 'item-4' }), /TTS is busy: queue is full/)

  await assert.rejects(ipc.invoke('spiritagent:media:tts', { text: 'item-5' }), /TTS is busy: queue is full/)

  finishRunning()
  const [res1, res2, res3] = await Promise.all([task1, task2, task3])
  assert.equal((res1 as { mimeType: string }).mimeType, 'audio/mpeg')
  assert.equal((res2 as { mimeType: string }).mimeType, 'audio/mpeg')
  assert.equal((res3 as { mimeType: string }).mimeType, 'audio/mpeg')
})

test('TTS local engine is also bounded by the queue', async () => {
  let finishRunning: () => void = () => {}

  const runningPromise = new Promise<void>(resolve => {
    finishRunning = resolve
  })

  const bridge: BridgeSpy = {
    calls: [],
    getTools: () => [toolSchema('text_to_speech')],
    invoke: async (name, args) => {
      bridge.calls.push({ args, name })
      await runningPromise

      return { path: tmpWav, success: true }
    }
  }

  const ipc = setup({ bridge, tts: 'local', ttsMaxQueueSize: 1 })

  const task1 = ipc.invoke('spiritagent:media:tts', { text: 'local-1' })
  const task2 = ipc.invoke('spiritagent:media:tts', { text: 'local-2' })

  await assert.rejects(ipc.invoke('spiritagent:media:tts', { text: 'local-3' }), /TTS is busy: queue is full/)

  finishRunning()
  await Promise.all([task1, task2])
})

test('TTS memory and disk cache hits do not consume queue capacity or delay backend calls', async () => {
  const home = makeHome()
  const dir = path.join(home, 'audio', 'tts-cache', 'zh')
  fs.mkdirSync(dir, { recursive: true })
  fs.writeFileSync(path.join(dir, `${cacheKey('冰糖', 'disk-item')}.mp3`), Buffer.from('disk-audio'))

  let cloudCalls = 0
  global.fetch = (async () => {
    cloudCalls += 1

    return makeFakeResponse({ bytes: Buffer.from('cloud-audio'), contentType: 'audio/mpeg' })
  }) as unknown as typeof globalThis.fetch

  const ipc = setup({ minTtsIntervalMs: 1000, spiritagentHome: home, tts: 'cloud', ttsMaxQueueSize: 0 })

  ttsAudioCache.set('冰糖::zh::memory-item', {
    dataUrl: 'data:audio/mpeg;base64,bWVtb3J5',
    expiresAt: Date.now() + 60000,
    mimeType: 'audio/mpeg'
  })

  const memRes = await ipc.invoke('spiritagent:media:tts', { text: 'memory-item', voice: '冰糖' })
  assert.equal((memRes as { dataUrl: string }).dataUrl, 'data:audio/mpeg;base64,bWVtb3J5')

  const diskRes = await ipc.invoke('spiritagent:media:tts', { persist: true, text: 'disk-item', voice: '冰糖' })
  assert.equal(
    (diskRes as { dataUrl: string }).dataUrl,
    `data:audio/mpeg;base64,${Buffer.from('disk-audio').toString('base64')}`
  )

  assert.equal(cloudCalls, 0, 'cache hits must never call cloud or consume backend quota')
})
