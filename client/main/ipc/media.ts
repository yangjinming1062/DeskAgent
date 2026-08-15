import fs from 'node:fs'

import type { IpcMain } from 'electron'

import type { MediaSttPayload, MediaTtsPayload } from '../shared/ipc-contracts'
import { dataUrlFromBuffer, dataUrlToBuffer } from '../shared/mime'
import { sleep } from '../shared/utils'

import { createTtsDiskCache } from './tts-disk-cache'

export const STT_TIMEOUT_MS = 60_000
export const TTS_TIMEOUT_MS = 60_000
export const TTS_MAX_TEXT_CHARS = 4000
export const STT_MAX_AUDIO_BYTES = 24 * 1024 * 1024
export const CONFIG_CACHE_TTL_MS = 10_000
export const DEFAULT_TTS_LANGUAGE = 'zh'
export const DEFAULT_STT_LANGUAGE = 'zh'
export const MIN_TTS_INTERVAL_MS = 750

let ttsSeq = 0
let sttSeq = 0
let lastTtsTime = 0
let ttsChain: Promise<unknown> = Promise.resolve()

function scheduleTtsBackend<T>(fn: () => Promise<T>): Promise<T> {
  const next = ttsChain.then(async () => {
    const now = Date.now()
    const waitMs = MIN_TTS_INTERVAL_MS - (now - lastTtsTime)

    if (waitMs > 0) {
      await sleep(waitMs)
    }

    lastTtsTime = Date.now()

    return await fn()
  })

  ttsChain = next.catch(() => {})

  return next
}

function decodeDataUrl(dataUrl?: string): { data: Buffer; mime: string } {
  const match = /^data:([^;,]+)?(;base64)?,(.*)$/s.exec(String(dataUrl || ''))

  if (!match) {
    throw new Error('Expected a base64 data URL')
  }

  const mime = match[1] || 'application/octet-stream'

  return { data: dataUrlToBuffer(dataUrl || ''), mime }
}

async function postMultipart({
  form,
  timeoutMs,
  token,
  url
}: {
  form: FormData
  timeoutMs: number
  token?: string
  url: string
}): Promise<{ body: Buffer; contentType: string; headers: Headers }> {
  const res = await fetch(url, {
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

export interface RunnerBridgeLike {
  getTools?: () => Record<string, unknown>[]
  invoke?: <T = unknown>(name: string, args?: Record<string, unknown>) => Promise<T>
}

function localToolAvailable(bridge: RunnerBridgeLike | null | undefined, toolName: string): boolean {
  if (!bridge) {
    return false
  }

  const tools = bridge.getTools?.()

  if (!Array.isArray(tools)) {
    return false
  }

  return tools.some(t => {
    const func = t?.function as { name?: string } | undefined

    return func?.name === toolName || t?.name === toolName
  })
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

interface LocalSttResult {
  error?: string
  success?: boolean
  text?: string
}

async function tryLocalStt({
  bridge,
  data,
  language,
  mime
}: {
  bridge: RunnerBridgeLike | null | undefined
  data: Buffer
  language?: string
  mime: string
}): Promise<{ error?: Error; ok: boolean; value?: { text: string } }> {
  try {
    if (!bridge?.invoke) {
      return { error: new Error('Runner bridge invoke is unavailable'), ok: false }
    }

    const result = await bridge.invoke<LocalSttResult>('speech_to_text', {
      audio_base64: data.toString('base64'),
      mime_type: mime,
      ...(language ? { language } : {})
    })

    if (result && result.success === true && typeof result.text === 'string') {
      return { ok: true, value: { text: result.text } }
    }

    const msg = result?.error ? String(result.error) : 'local STT returned no text'

    return { error: new Error(msg), ok: false }
  } catch (e: unknown) {
    return { error: e instanceof Error ? e : new Error(String(e)), ok: false }
  }
}

interface LocalTtsResult {
  engine?: string
  error?: string
  path?: string
  success?: boolean
  voice?: string
}

async function tryLocalTts({ bridge, text }: { bridge: RunnerBridgeLike | null | undefined; text: string }): Promise<{
  error?: Error
  ok: boolean
  value?: { dataUrl: string; engine: string; mimeType: string; voice: string }
}> {
  try {
    if (!bridge?.invoke) {
      return { error: new Error('Runner bridge invoke is unavailable'), ok: false }
    }

    const result = await bridge.invoke<LocalTtsResult>('text_to_speech', { text })

    if (result && result.success === true && result.path) {
      const buf = fs.readFileSync(result.path)

      return {
        ok: true,
        value: {
          dataUrl: `data:audio/wav;base64,${buf.toString('base64')}`,
          engine: result.engine || 'unknown',
          mimeType: 'audio/wav',
          voice: result.voice || '(default)'
        }
      }
    }

    const msg = result?.error ? String(result.error) : 'local TTS returned no audio'

    return { error: new Error(msg), ok: false }
  } catch (e: unknown) {
    return { error: e instanceof Error ? e : new Error(String(e)), ok: false }
  }
}

async function sttViaBackend({
  data,
  ensureBackend,
  filename,
  language,
  mime
}: {
  data: Buffer
  ensureBackend: () => Promise<{ baseUrl: string; token?: null | string }>
  filename?: string
  language?: string
  mime: string
}): Promise<string> {
  const connection = await ensureBackend()
  const form = new FormData()
  const blob = new Blob([data], { type: mime })
  form.append('file', blob, filename || (mime.includes('webm') ? 'audio.webm' : 'audio.wav'))

  const qs = language ? `?language=${encodeURIComponent(language)}` : ''
  const url = `${connection.baseUrl}/api/media/stt${qs}`

  const { body } = await postMultipart({
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
  language,
  text,
  voice
}: {
  ensureBackend: () => Promise<{ baseUrl: string; token?: null | string }>
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

  const res = await fetch(url, {
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
  const voiceOut = res.headers.get('x-deskagent-voice') || undefined

  return { dataUrl: dataUrlFromBuffer(buf, mime), mimeType: mime, voiceOut }
}

export type EngineMode = 'auto' | 'cloud' | 'local'

export interface EnginePrefs {
  expiresAt: number
  stt: EngineMode
  sttEnabled: boolean
  sttSilentFallback: boolean
  tts: EngineMode
}

export function createEnginePrefsCache({
  ensureBackend,
  ttlMs = CONFIG_CACHE_TTL_MS
}: {
  ensureBackend: () => Promise<{ baseUrl: string; token?: null | string }>
  ttlMs?: number
}): () => Promise<EnginePrefs> {
  let cached: null | EnginePrefs = null

  return async function getEnginePrefs(): Promise<EnginePrefs> {
    const now = Date.now()

    if (cached && cached.expiresAt > now) {
      return cached
    }

    try {
      const connection = await ensureBackend()

      const res = await fetch(`${connection.baseUrl}/api/config`, {
        headers: { ...(connection.token ? { Authorization: `Bearer ${connection.token}` } : {}) },
        signal: AbortSignal.timeout(10_000)
      })

      if (!res.ok) {
        throw new Error(`${res.status} /api/config`)
      }

      const body = (await res.json()) as {
        config?: {
          stt?: { enabled?: boolean; engine?: EngineMode; silent_fallback?: boolean }
          tts?: { engine?: EngineMode }
        }
      }

      const config = body?.config || {}
      cached = {
        expiresAt: now + ttlMs,
        stt: config.stt?.engine || 'auto',
        sttEnabled: config.stt?.enabled !== false,
        sttSilentFallback: config.stt?.silent_fallback !== false,
        tts: config.tts?.engine || 'auto'
      }
    } catch {
      cached = { expiresAt: now + ttlMs, stt: 'auto', sttEnabled: true, sttSilentFallback: true, tts: 'auto' }
    }

    return cached
  }
}

export const TTS_CACHE_MAX_ENTRIES = 100
export const TTS_CACHE_TTL_MS = 10 * 60 * 1000 // 10 minutes

export const ttsAudioCache: Map<string, { dataUrl: string; expiresAt: number; mimeType: string }> = new Map()
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

export interface MediaIpcDeps {
  deskagentHome?: null | string
  ensureBackend: () => Promise<{ baseUrl: string; token?: null | string }>
  getEnginePrefs?: () => Promise<EnginePrefs>
  getRunnerBridge?: () => RunnerBridgeLike | null | undefined
  ipcMain: IpcMain
  log?: (msg: string) => void
}

export function registerMediaIpc({
  deskagentHome,
  ensureBackend,
  getEnginePrefs,
  getRunnerBridge,
  ipcMain,
  log = () => {}
}: MediaIpcDeps): void {
  const resolvePrefs = typeof getEnginePrefs === 'function' ? getEnginePrefs : createEnginePrefsCache({ ensureBackend })
  const bridge = () => (typeof getRunnerBridge === 'function' ? getRunnerBridge() : null)
  const diskCache = createTtsDiskCache({ deskagentHome })

  ipcMain.handle('deskagent:media:stt', async (_event, payload?: MediaSttPayload) => {
    const sttId = ++sttSeq
    const { data, mime } = decodeDataUrl(payload?.dataUrl)

    if (data.length > STT_MAX_AUDIO_BYTES) {
      throw new Error(`Audio too large (${data.length} bytes; max ${STT_MAX_AUDIO_BYTES})`)
    }

    const language = payload?.language || DEFAULT_STT_LANGUAGE
    const filename = payload?.filename
    const context = payload?.context || 'default'
    const startedAt = Date.now()

    const sttLog = makeLog(log, '[stt]', {
      bytes: data.length,
      ctx: context,
      id: sttId,
      lang: language,
      mime
    })

    sttLog('start')

    const prefs = await resolvePrefs()

    if (!prefs.sttEnabled) {
      sttLog('done', { disabled: true, ms: Date.now() - startedAt })
      throw new Error('STT is disabled in configuration')
    }

    const engine = prefs.stt
    let fellBackToLocal = false

    if (engine === 'auto') {
      if (localToolAvailable(bridge(), 'speech_to_text')) {
        const res = await tryLocalStt({ bridge: bridge(), data, language, mime })

        if (res.ok && res.value) {
          sttLog('done', {
            chars: res.value.text.length,
            ms: Date.now() - startedAt,
            route: 'local'
          })

          return { text: res.value.text }
        }

        if (!prefs.sttSilentFallback) {
          sttLog('done', { error: res.error?.message, ms: Date.now() - startedAt, route: 'local' })
          throw res.error
        }

        fellBackToLocal = true
        sttLog('fallback', { from: 'local', reason: res.error?.message, to: 'cloud' })
      }

      try {
        const text = await sttViaBackend({ data, ensureBackend, filename, language, mime })
        sttLog('done', {
          chars: text.length,
          ms: Date.now() - startedAt,
          route: 'cloud',
          ...(fellBackToLocal ? { silent_fallback_used: true } : {})
        })

        return { text }
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err)
        sttLog('done', { error: msg, ms: Date.now() - startedAt, route: 'cloud' })
        throw err
      }
    } else if (engine === 'cloud') {
      const text = await sttViaBackend({ data, ensureBackend, filename, language, mime })
      sttLog('done', { chars: text.length, ms: Date.now() - startedAt, route: 'cloud' })

      return { text }
    }

    if (localToolAvailable(bridge(), 'speech_to_text')) {
      const res = await tryLocalStt({ bridge: bridge(), data, language, mime })

      if (res.ok && res.value) {
        sttLog('done', {
          chars: res.value.text.length,
          ms: Date.now() - startedAt,
          route: 'local'
        })

        return { text: res.value.text }
      }

      sttLog('done', { error: res.error?.message, ms: Date.now() - startedAt, route: 'local' })
      throw res.error
    }

    sttLog('done', { error: 'Local STT unavailable', ms: Date.now() - startedAt, route: 'local' })

    if (engine === 'local') {
      throw new Error('Local STT unavailable: runner not connected or speech_to_text tool missing')
    }

    throw new Error('STT failed: cloud unreachable and local STT unavailable')
  })

  ipcMain.handle('deskagent:media:tts', async (_event, payload?: MediaTtsPayload) => {
    const ttsId = ++ttsSeq
    const text = String(payload?.text || '').trim()

    if (!text) {
      throw new Error('text is required')
    }

    if (text.length > TTS_MAX_TEXT_CHARS) {
      throw new Error(`Text too long (${text.length} chars; max ${TTS_MAX_TEXT_CHARS})`)
    }

    const isDesigned = (payload?.voice || '').startsWith('designed:')
    const voice = payload?.voice || ''
    const language = DEFAULT_TTS_LANGUAGE
    const persist = payload?.persist === true
    const context = payload?.context || 'default'
    const startedAt = Date.now()

    const ttsLog = makeLog(log, '[tts]', {
      chars: text.length,
      ctx: context,
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

    const task = synthesizeTts({ cacheKey, isDesigned, language, persist, startedAt, text, ttsLog, voice })
    inflightTts.set(cacheKey, task)

    try {
      return await task
    } finally {
      inflightTts.delete(cacheKey)
    }
  })

  async function synthesizeTts({
    cacheKey,
    isDesigned,
    language,
    persist,
    startedAt,
    text,
    ttsLog,
    voice
  }: {
    cacheKey: string
    isDesigned: boolean
    language: string
    persist: boolean
    startedAt: number
    text: string
    ttsLog: (event: string, extra?: Record<string, unknown>) => void
    voice: string
  }): Promise<{ dataUrl: string; mimeType: string }> {
    if (persist) {
      const hit = await diskCache.read({ language, text, voice })

      if (hit) {
        const value = { dataUrl: dataUrlFromBuffer(hit, 'audio/mpeg'), mimeType: 'audio/mpeg' }
        setCachedTts(cacheKey, value)
        ttsLog('done', { bytes: hit.length, cached: true, ms: Date.now() - startedAt, route: 'disk' })

        return value
      }
    }

    const prefs = await resolvePrefs()
    const engine = isDesigned ? 'cloud' : prefs.tts

    let fellBackToLocal = false

    const callBackendThrottled = async () =>
      scheduleTtsBackend(() => ttsViaBackend({ ensureBackend, language, text, voice }))

    const finishCloud = async (result: { dataUrl: string; mimeType: string; voiceOut?: string }) => {
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
    }

    if (engine === 'auto') {
      try {
        return await finishCloud(await callBackendThrottled())
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err)
        fellBackToLocal = true
        ttsLog('fallback', { from: 'cloud', reason: msg, to: 'local' })
      }
    } else if (engine === 'cloud') {
      return await finishCloud(await callBackendThrottled())
    }

    if (localToolAvailable(bridge(), 'text_to_speech')) {
      const res = await tryLocalTts({ bridge: bridge(), text })

      if (res.ok && res.value) {
        setCachedTts(cacheKey, { dataUrl: res.value.dataUrl, mimeType: res.value.mimeType })
        ttsLog('done', {
          engine: res.value.engine,
          mime: res.value.mimeType,
          ms: Date.now() - startedAt,
          route: 'local',
          voice: res.value.voice,
          voice_requested: voice || null,
          ...(fellBackToLocal ? { silent_fallback_used: true } : {})
        })

        return { dataUrl: res.value.dataUrl, mimeType: res.value.mimeType }
      }

      ttsLog('done', { error: res.error?.message, ms: Date.now() - startedAt, route: 'local' })
      throw res.error
    }

    ttsLog('done', { error: 'Local TTS unavailable', ms: Date.now() - startedAt, route: 'local' })

    if (engine === 'local') {
      throw new Error('Local TTS unavailable: runner not connected or text_to_speech tool missing')
    }

    throw new Error('TTS failed: cloud unreachable and local TTS unavailable')
  }
}
