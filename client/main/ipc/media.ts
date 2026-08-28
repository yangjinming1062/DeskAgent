import fs from 'node:fs'
import path from 'node:path'

import {
  type AttachmentVideoUploadPayload,
  type AttachmentVideoUploadResult,
  IPC,
  type MediaSttPayload,
  type MediaTtsPayload
} from '@ipc/contracts'
import type { IpcMain } from 'electron'

import { resolveReadableFileForIpc } from '../security/hardening'
import { dataUrlFromBuffer, dataUrlToBuffer, parseDataUrl } from '../shared/mime'
import { sleep } from '../shared/utils'

import { createTtsDiskCache } from './tts-disk-cache'

const STT_TIMEOUT_MS = 60_000
const TTS_TIMEOUT_MS = 60_000
const TTS_MAX_TEXT_CHARS = 4000
const STT_MAX_AUDIO_BYTES = 24 * 1024 * 1024
const ATTACH_VIDEO_MAX_BYTES = 512 * 1024 * 1024
const ATTACH_VIDEO_TIMEOUT_MS = 120_000
const DEFAULT_TTS_LANGUAGE = 'zh'
const DEFAULT_STT_LANGUAGE = 'zh'
const MIN_TTS_INTERVAL_MS = 750
const STT_MAX_CONCURRENCY = 2
const STT_BURST = 4
const STT_REFILL_RATE = 2
const TTS_MAX_QUEUE_SIZE = 8

let ttsSeq = 0
let sttSeq = 0

interface TtsQueueItem<T> {
  fn: () => Promise<T>
  reject: (err: unknown) => void
  resolve: (value: T) => void
}

class SttLimiter {
  private activeCount = 0
  private readonly burst: number
  private lastRefill: number
  private readonly maxConcurrency: number
  private readonly refillRate: number
  private tokens: number

  constructor({
    burst = STT_BURST,
    maxConcurrency = STT_MAX_CONCURRENCY,
    refillRate = STT_REFILL_RATE
  }: {
    burst?: number
    maxConcurrency?: number
    refillRate?: number
  } = {}) {
    this.maxConcurrency = maxConcurrency
    this.burst = burst
    this.refillRate = refillRate
    this.tokens = burst
    this.lastRefill = Date.now()
  }

  acquire(): () => void {
    const now = Date.now()
    const elapsed = (now - this.lastRefill) / 1000
    this.tokens = Math.min(this.burst, this.tokens + elapsed * this.refillRate)
    this.lastRefill = now

    if (this.tokens < 1) {
      throw new Error('STT is busy: rate limit exceeded')
    }

    if (this.activeCount >= this.maxConcurrency) {
      throw new Error('STT is busy: maximum concurrency reached')
    }

    this.tokens -= 1
    this.activeCount += 1

    let released = false

    return () => {
      if (!released) {
        released = true
        this.activeCount = Math.max(0, this.activeCount - 1)
      }
    }
  }
}

class BoundedTtsQueue {
  private isProcessing = false
  private lastCloudTtsTime = 0
  private readonly maxQueueSize: number
  private readonly minCloudIntervalMs: number
  private readonly queue: Array<TtsQueueItem<unknown>> = []

  constructor({
    maxQueueSize = TTS_MAX_QUEUE_SIZE,
    minCloudIntervalMs = MIN_TTS_INTERVAL_MS
  }: {
    maxQueueSize?: number
    minCloudIntervalMs?: number
  } = {}) {
    this.maxQueueSize = maxQueueSize
    this.minCloudIntervalMs = minCloudIntervalMs
  }

  enqueue<T>(fn: () => Promise<T>): Promise<T> {
    if (this.queue.length >= this.maxQueueSize) {
      throw new Error('TTS is busy: queue is full')
    }

    return new Promise<T>((resolve, reject) => {
      this.queue.push({
        fn: fn as () => Promise<unknown>,
        reject,
        resolve: resolve as (value: unknown) => void
      })
      void this.processNext()
    })
  }

  async throttleCloud(): Promise<void> {
    const now = Date.now()
    const waitMs = this.minCloudIntervalMs - (now - this.lastCloudTtsTime)

    if (waitMs > 0) {
      await sleep(waitMs)
    }

    this.lastCloudTtsTime = Date.now()
  }

  private async processNext(): Promise<void> {
    if (this.isProcessing || this.queue.length === 0) {
      return
    }

    this.isProcessing = true
    const item = this.queue.shift()

    if (!item) {
      this.isProcessing = false

      return
    }

    try {
      const result = await item.fn()
      item.resolve(result)
    } catch (err) {
      item.reject(err)
    } finally {
      this.isProcessing = false
      void this.processNext()
    }
  }
}

function decodeDataUrl(dataUrl?: string): { data: Buffer; mime: string } {
  const parsed = parseDataUrl(dataUrl || '')

  return { data: parsed.data, mime: parsed.mime }
}

async function postMultipart({
  fetchImpl,
  form,
  timeoutMs,
  token,
  url
}: {
  fetchImpl?: typeof globalThis.fetch
  form: FormData
  timeoutMs: number
  token?: string
  url: string
}): Promise<{ body: Buffer; contentType: string; headers: Headers }> {
  const caller = fetchImpl || globalThis.fetch

  const res = await caller(url, {
    body: form,
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    method: 'POST',
    signal: AbortSignal.timeout(timeoutMs)
  })

  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`${res.status} ${new URL(url).pathname}: ${text || res.statusText}`)
  }

  const buf = Buffer.from(await res.arrayBuffer())

  return { body: buf, contentType: res.headers.get('content-type') || '', headers: res.headers }
}

function formatKv(
  prefix: string,
  event: string,
  base: Record<string, unknown>,
  extra: Record<string, unknown> = {}
): string {
  const fmt = ([k, v]: [string, unknown]) => `${k}=${typeof v === 'string' ? JSON.stringify(v) : String(v)}`

  const parts = [...Object.entries(base), ...Object.entries(extra)]
    .filter(([, v]) => v !== null && v !== undefined && v !== false)
    .map(fmt)

  return `${prefix} ${event} ${parts.join(' ')}`
}

function makeLog(
  log: (msg: string) => void,
  prefix: string,
  base: Record<string, unknown>
): (event: string, extra?: Record<string, unknown>) => void {
  return (event, extra = {}) => log(formatKv(prefix, event, base, extra))
}

async function sttViaBackend({
  data,
  ensureBackend,
  fetchImpl = globalThis.fetch,
  filename,
  language,
  mime
}: {
  data: Buffer
  ensureBackend: () => Promise<{ baseUrl: string; token?: null | string }>
  fetchImpl?: typeof globalThis.fetch
  filename?: string
  language?: string
  mime: string
}): Promise<string> {
  const connection = await ensureBackend()
  const form = new FormData()
  const blob = new Blob([data], { type: mime })
  const actualFilename = filename || (mime.includes('webm') ? 'audio.webm' : 'audio.wav')
  form.append('audio_file', blob, actualFilename)
  form.append('file', blob, actualFilename)

  const qs = language ? `?language=${encodeURIComponent(language)}` : ''
  const url = `${connection.baseUrl}/api/media/stt${qs}`

  const { body } = await postMultipart({
    fetchImpl,
    form,
    timeoutMs: STT_TIMEOUT_MS,
    token: connection.token || undefined,
    url
  })

  const parsed = JSON.parse(body.toString('utf8')) as { text?: string }

  if (typeof parsed?.text !== 'string') {
    throw new Error('Backend STT returned no text')
  }

  return parsed.text
}

async function ttsViaBackend({
  ensureBackend,
  fetchImpl,
  language,
  text,
  voice
}: {
  ensureBackend: () => Promise<{ baseUrl: string; token?: null | string }>
  fetchImpl?: typeof globalThis.fetch
  language?: string
  text: string
  voice?: string
}): Promise<{ dataUrl: string; mimeType: string; voiceOut?: string }> {
  const connection = await ensureBackend()
  const url = `${connection.baseUrl}/api/media/tts`

  const payload: Record<string, unknown> = { text }

  if (voice) {
    payload.voice = voice
  }

  if (language) {
    payload.language = language
  }

  const caller = fetchImpl || globalThis.fetch

  const res = await caller(url, {
    body: JSON.stringify(payload),
    headers: {
      'Content-Type': 'application/json',
      ...(connection.token ? { Authorization: `Bearer ${connection.token}` } : {})
    },
    method: 'POST',
    signal: AbortSignal.timeout(TTS_TIMEOUT_MS)
  })

  if (!res.ok) {
    const errText = await res.text().catch(() => '')
    throw new Error(`${res.status} /api/media/tts: ${errText || res.statusText}`)
  }

  const mime = res.headers.get('content-type') || 'audio/mpeg'
  const buf = Buffer.from(await res.arrayBuffer())
  // 后端实际返回的头名是 X-Voice-Used（api/v1/media.py TTS 端点）。
  const voiceOut = res.headers.get('x-voice-used') || undefined

  return { dataUrl: dataUrlFromBuffer(buf, mime), mimeType: mime, voiceOut }
}

const TTS_CACHE_MAX_ENTRIES = 100
const TTS_CACHE_TTL_MS = 10 * 60 * 1000

const ttsAudioCache: Map<string, { dataUrl: string; expiresAt: number; mimeType: string }> = new Map()
const inflightTts = new Map<string, Promise<{ dataUrl: string; mimeType: string }>>()

function getCachedTts(key: string): null | { dataUrl: string; expiresAt: number; mimeType: string } {
  const entry = ttsAudioCache.get(key)

  if (!entry) {
    return null
  }

  if (Date.now() > entry.expiresAt) {
    ttsAudioCache.delete(key)

    return null
  }

  ttsAudioCache.delete(key)
  ttsAudioCache.set(key, entry)

  return entry
}

function setCachedTts(key: string, value: { dataUrl: string; mimeType: string }): void {
  if (ttsAudioCache.size >= TTS_CACHE_MAX_ENTRIES) {
    const oldestKey = ttsAudioCache.keys().next().value

    if (oldestKey !== undefined) {
      ttsAudioCache.delete(oldestKey)
    }
  }

  ttsAudioCache.set(key, { ...value, expiresAt: Date.now() + TTS_CACHE_TTL_MS })
}

interface MediaIpcDeps {
  spiritagentHome?: null | string
  ensureBackend: () => Promise<{ baseUrl: string; token?: null | string }>
  fetchImpl?: typeof globalThis.fetch
  isSttEnabled: () => boolean
  ipcMain: IpcMain
  log?: (msg: string) => void
  minTtsIntervalMs?: number
  sttBurst?: number
  sttMaxConcurrency?: number
  sttRefillRate?: number
  ttsMaxQueueSize?: number
}

export function registerMediaIpc({
  spiritagentHome,
  ensureBackend,
  fetchImpl,
  isSttEnabled,
  ipcMain,
  log = () => {},
  minTtsIntervalMs,
  sttBurst,
  sttMaxConcurrency,
  sttRefillRate,
  ttsMaxQueueSize
}: MediaIpcDeps): void {
  const diskCache = createTtsDiskCache({ spiritagentHome })
  const sttLimiter = new SttLimiter({ burst: sttBurst, maxConcurrency: sttMaxConcurrency, refillRate: sttRefillRate })
  const ttsQueue = new BoundedTtsQueue({ maxQueueSize: ttsMaxQueueSize, minCloudIntervalMs: minTtsIntervalMs })

  ipcMain.handle(IPC.invoke.mediaStt, async (_event, payload?: MediaSttPayload) => {
    const sttId = ++sttSeq
    const { data, mime } = decodeDataUrl(payload?.dataUrl)

    if (data.length > STT_MAX_AUDIO_BYTES) {
      throw new Error(`Audio too large (${data.length} bytes; max ${STT_MAX_AUDIO_BYTES})`)
    }

    const release = sttLimiter.acquire()

    try {
      const startedAt = Date.now()

      const sttLog = makeLog(log, '[stt]', {
        bytes: data.length,
        ctx: payload?.context || 'default',
        id: sttId,
        lang: payload?.language || DEFAULT_STT_LANGUAGE,
        mime
      })

      sttLog('start')

      if (!isSttEnabled()) {
        sttLog('done', { disabled: true, ms: Date.now() - startedAt })
        throw new Error('STT is disabled in configuration')
      }

      const text = await sttViaBackend({
        data,
        ensureBackend,
        fetchImpl,
        filename: payload?.filename,
        language: payload?.language || DEFAULT_STT_LANGUAGE,
        mime
      })

      sttLog('done', {
        chars: text.length,
        ms: Date.now() - startedAt,
        route: 'cloud'
      })

      return { text }
    } finally {
      release()
    }
  })

  ipcMain.handle(IPC.invoke.mediaTts, async (_event, payload?: MediaTtsPayload) => {
    const ttsId = ++ttsSeq
    const text = String(payload?.text || '').trim()

    if (!text) {
      throw new Error('text is required')
    }

    if (text.length > TTS_MAX_TEXT_CHARS) {
      throw new Error(`Text too long (${text.length} chars; max ${TTS_MAX_TEXT_CHARS})`)
    }

    const voice = payload?.voice || ''
    const language = DEFAULT_TTS_LANGUAGE
    const persist = payload?.persist === true
    const startedAt = Date.now()

    const ttsLog = makeLog(log, '[tts]', {
      chars: text.length,
      ctx: payload?.context || 'default',
      id: ttsId,
      lang: language,
      persisted: persist,
      voice: voice || null
    })

    ttsLog('start')

    const cacheKey = `${voice}::${language}::${text}`
    const cached = getCachedTts(cacheKey)

    if (cached) {
      ttsLog('done', { cached: true, ms: Date.now() - startedAt, route: 'memory' })

      return { dataUrl: cached.dataUrl, mimeType: cached.mimeType }
    }

    const pending = inflightTts.get(cacheKey)

    if (pending) {
      ttsLog('join', { route: 'inflight' })

      return await pending
    }

    const task = ttsQueue.enqueue(async () => {
      if (persist) {
        const hit = await diskCache.read({ language, text, voice })

        if (hit) {
          const value = { dataUrl: dataUrlFromBuffer(hit, 'audio/mpeg'), mimeType: 'audio/mpeg' }
          setCachedTts(cacheKey, value)
          ttsLog('done', { bytes: hit.length, cached: true, ms: Date.now() - startedAt, route: 'disk' })

          return value
        }
      }

      const result = await ttsViaBackend({ ensureBackend, fetchImpl, language, text, voice })
      const value = { dataUrl: result.dataUrl, mimeType: result.mimeType }
      setCachedTts(cacheKey, value)

      if (persist) {
        await diskCache.write({
          buffer: dataUrlToBuffer(result.dataUrl),
          language,
          mimeType: result.mimeType,
          text,
          voice
        })
      }

      ttsLog('done', {
        mime: result.mimeType,
        ms: Date.now() - startedAt,
        persisted: persist,
        route: 'cloud',
        voice_out: result.voiceOut || null
      })

      return value
    })

    inflightTts.set(cacheKey, task)

    try {
      return await task
    } finally {
      inflightTts.delete(cacheKey)
    }
  })

  ipcMain.handle(
    IPC.invoke.mediaVideoUpload,
    async (_event, payload: AttachmentVideoUploadPayload): Promise<AttachmentVideoUploadResult> => {
      const { resolvedPath } = await resolveReadableFileForIpc(payload.path, {
        maxBytes: ATTACH_VIDEO_MAX_BYTES,
        purpose: 'Video attach'
      })

      const data = await fs.promises.readFile(resolvedPath)
      const connection = await ensureBackend()
      const form = new FormData()
      const blob = new Blob([data], { type: 'application/octet-stream' })

      form.append('file', blob, path.basename(resolvedPath))
      form.append('session_id', payload.sessionId)

      const { body } = await postMultipart({
        fetchImpl,
        form,
        timeoutMs: ATTACH_VIDEO_TIMEOUT_MS,
        token: connection.token || undefined,
        url: `${connection.baseUrl}/api/media/videos`
      })

      const parsed = JSON.parse(body.toString('utf8')) as {
        file_id?: string
        mime?: string
        size?: number
        url?: string
      }

      if (typeof parsed?.url !== 'string' || !parsed.url) {
        throw new Error('Backend video upload returned no url')
      }

      return {
        fileId: parsed.file_id || '',
        mime: parsed.mime || 'video/mp4',
        size: parsed.size ?? data.length,
        url: parsed.url
      }
    }
  )
}
