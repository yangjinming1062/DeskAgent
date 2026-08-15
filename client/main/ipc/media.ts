import fs from 'node:fs'

import type { IpcMain } from 'electron'

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

let ttsSeq = 0
let sttSeq = 0

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

function localToolAvailable(bridge: any, toolName: string): boolean {
  if (!bridge) {
    return false
  }

  const tools = bridge.getTools()

  if (!Array.isArray(tools)) {
    return false
  }

  return tools.some((t: any) => (t?.function?.name || t?.name) === toolName)
}

function formatKv(prefix: string, event: string, base: Record<string, any>, extra: Record<string, any> = {}): string {
  const fmt = ([k, v]: [string, any]) => `${k}=${typeof v === 'string' ? JSON.stringify(v) : v}`

  const parts = [...Object.entries(base), ...Object.entries(extra)]
    .filter(([, v]) => v !== null && v !== undefined && v !== false)
    .map(fmt)

  return `${prefix} ${event} ${parts.join(' ')}`
}

function makeLog(
  log: (msg: string) => void,
  prefix: string,
  base: Record<string, any>
): (event: string, extra?: Record<string, any>) => void {
  return (event, extra = {}) => log(formatKv(prefix, event, base, extra))
}

async function tryLocalStt({
  bridge,
  data,
  language,
  mime
}: {
  bridge: any
  data: Buffer
  language?: string
  mime: string
}): Promise<{ error?: Error; ok: boolean; value?: { text: string } }> {
  try {
    const result = await bridge.invoke('speech_to_text', {
      audio_base64: data.toString('base64'),
      mime_type: mime,
      ...(language ? { language } : {})
    })

    if (result && result.success === true && typeof result.text === 'string') {
      return { ok: true, value: { text: result.text } }
    }

    const msg = result?.error ? String(result.error) : 'local STT returned no text'

    return { error: new Error(msg), ok: false }
  } catch (e: any) {
    return { error: e instanceof Error ? e : new Error(String(e)), ok: false }
  }
}

async function tryLocalTts({ bridge, text }: { bridge: any; text: string }): Promise<{
  error?: Error
  ok: boolean
  value?: { dataUrl: string; engine: string; mimeType: string; voice: string }
}> {
  try {
    const result = await bridge.invoke('text_to_speech', { text })

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
  } catch (e: any) {
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
  ensureBackend: () => Promise<{ baseUrl: string; token?: string }>
  filename: string
  language?: string
  mime: string
}): Promise<{ text: string }> {
  const connection = await ensureBackend()
  const form = new FormData()
  form.set('audio_file', new Blob([data], { type: mime }), filename)

  if (language) {
    form.set('language', language)
  }

  const { body } = await postMultipart({
    form,
    timeoutMs: STT_TIMEOUT_MS,
    token: connection.token,
    url: `${connection.baseUrl}/api/media/stt`
  })

  const parsed = JSON.parse(body.toString('utf8'))

  return { text: parsed.text || '' }
}

const MIN_TTS_INTERVAL_MS = 4000
let lastTtsCallAt = performance.now()

export async function ttsViaBackend({
  ensureBackend,
  language,
  text,
  voice
}: {
  ensureBackend: () => Promise<{ baseUrl: string; token?: string }>
  language?: string
  text: string
  voice: string
}): Promise<{ dataUrl: string; mimeType: string; voiceOut: string }> {
  const sinceLast = performance.now() - lastTtsCallAt
  lastTtsCallAt = performance.now()

  if (sinceLast < MIN_TTS_INTERVAL_MS) {
    await sleep(MIN_TTS_INTERVAL_MS - sinceLast)
  }

  const connection = await ensureBackend()
  const form = new FormData()
  form.set('text', text)
  form.set('voice', voice)
  form.set('language', language || DEFAULT_TTS_LANGUAGE)

  const { body, contentType, headers } = await postMultipart({
    form,
    timeoutMs: TTS_TIMEOUT_MS,
    token: connection.token,
    url: `${connection.baseUrl}/api/media/tts`
  })

  const mime = contentType.split(';')[0].trim() || 'audio/mpeg'
  let voiceOut = headers.get('x-voice-used') || voice

  try {
    voiceOut = decodeURIComponent(voiceOut)
  } catch {
    // Keep raw
  }

  return { dataUrl: dataUrlFromBuffer(body, mime), mimeType: mime, voiceOut }
}

export interface EnginePrefs {
  expiresAt: number
  stt: string
  sttEnabled: boolean
  sttSilentFallback: boolean
  tts: string
}

export function createEnginePrefsCache({
  ensureBackend,
  ttlMs = CONFIG_CACHE_TTL_MS
}: {
  ensureBackend: () => Promise<{ baseUrl: string; token?: string }>
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

      const body = (await res.json()) as any
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
  ensureBackend: () => Promise<{ baseUrl: string; token?: string }>
  getEnginePrefs?: () => Promise<EnginePrefs>
  getRunnerBridge?: () => any
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

  ipcMain.handle('deskagent:media:stt', async (_event, payload) => {
    const sttId = ++sttSeq
    const { data, mime } = decodeDataUrl(payload?.dataUrl)

    if (data.length > STT_MAX_AUDIO_BYTES) {
      throw new Error(`Audio too large (${data.length} bytes; max ${STT_MAX_AUDIO_BYTES})`)
    }

    const filename = payload?.filename || `recording.${(mime.split('/')[1] || 'webm').split(';')[0]}`
    const context = typeof payload?.context === 'string' ? payload.context : null
    const language = typeof payload?.language === 'string' && payload.language ? payload.language : DEFAULT_STT_LANGUAGE

    const prefs = await resolvePrefs()

    if (prefs.sttEnabled === false) {
      throw new Error('STT is disabled in settings')
    }

    const engine = prefs.stt
    const silentFallback = prefs.sttSilentFallback

    const sttLog = makeLog(log, `[stt#${sttId}]`, {
      context: context || null,
      engine_pref: engine,
      ...(silentFallback ? {} : { silent_fallback: false }),
      ...(mime ? { mime } : {})
    })

    const startedAt = Date.now()

    let fellBackFromLocal = false

    if (engine !== 'cloud') {
      if (localToolAvailable(bridge(), 'speech_to_text')) {
        const res = await tryLocalStt({ bridge: bridge(), data, language, mime })

        if (res.ok && res.value) {
          sttLog('done', { ms: Date.now() - startedAt, route: 'local', text_chars: res.value.text.length })

          return res.value
        }

        if (engine === 'local' || !silentFallback) {
          sttLog('done', { error: res.error?.message, ms: Date.now() - startedAt, route: 'local' })
          throw res.error
        }

        sttLog('fallback', { from: 'local', reason: res.error?.message, to: 'cloud' })
        fellBackFromLocal = true
      } else if (engine === 'local') {
        sttLog('done', { error: 'Local STT unavailable', ms: Date.now() - startedAt, route: 'local' })
        throw new Error('Local STT unavailable: runner not connected or speech_to_text tool missing')
      } else {
        fellBackFromLocal = true
      }
    }

    const result = await sttViaBackend({ data, ensureBackend, filename, language, mime })
    sttLog('done', {
      language,
      ms: Date.now() - startedAt,
      route: 'cloud',
      text_chars: result.text.length,
      ...(silentFallback && engine === 'auto' && fellBackFromLocal ? { silent_fallback_used: true } : {})
    })

    return result
  })

  ipcMain.handle('deskagent:media:tts', async (_event, payload) => {
    const ttsId = ++ttsSeq
    const text = String(payload?.text || '')

    if (!text) {
      throw new Error('text is required')
    }

    if (text.length > TTS_MAX_TEXT_CHARS) {
      throw new Error(`text exceeds ${TTS_MAX_TEXT_CHARS} chars`)
    }

    const voice = String(payload?.voice || '')
    const language = typeof payload?.language === 'string' && payload.language ? payload.language : DEFAULT_TTS_LANGUAGE
    const context = typeof payload?.context === 'string' ? payload.context : null
    const persist = payload?.persist === true

    const cacheKey = `${voice}::${language}::${text}`
    const startedAt = Date.now()

    const VOICEDESIGN_PREFIX = 'mimo_voicedesign:'
    const isDesigned = voice.startsWith(VOICEDESIGN_PREFIX)

    const ttsLog = makeLog(log, `[tts#${ttsId}]`, {
      context: context || null,
      is_designed: isDesigned,
      voice_in: voice || ''
    })

    const cached = getCachedTts(cacheKey)

    if (cached) {
      ttsLog('done', { cached: true, mime: cached.mimeType, ms: Date.now() - startedAt, route: 'cache' })

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
    ttsLog: (event: string, extra?: Record<string, any>) => void
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
        return await finishCloud(await ttsViaBackend({ ensureBackend, language, text, voice }))
      } catch (err: any) {
        fellBackToLocal = true
        ttsLog('fallback', { from: 'cloud', reason: err.message, to: 'local' })
      }
    } else if (engine === 'cloud') {
      return await finishCloud(await ttsViaBackend({ ensureBackend, language, text, voice }))
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
