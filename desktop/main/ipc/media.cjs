'use strict'

// STT/TTS routing hub. Each media request is dispatched to one of three
// engines per the user's per-service preference (`stt.engine` / `tts.engine`
// in the backend config):
//   - "auto"   (default): local Runner engine first; on local failure or
//               unavailability, fall back to the Backend cloud engine.
//   - "local": local Runner engine only; failures are NOT masked by a cloud
//               fallback — the renderer's existing STT/TTS failure handling
//               takes over (COMPANION_DESIGN §4.5 "always-fallback-to-text").
//   - "cloud": Backend cloud engine always.
//
// The renderer IPC contract (`media.stt {dataUrl,filename}` /
// `media.tts {text,voice}`) is unchanged; the local/cloud contract
// translation (dataUrl ↔ base64 / local WAV path) happens here.
//
// Every STT/TTS request emits one `done` line on the dev terminal via
// the injected `log` sink; auto-fallbacks add one extra `fallback` line.
// Format: `[tts#N] done voice_in="..." engine_pref=auto context=... route=local engine=pyttsx3 voice="..." mime=audio/wav bytes=N ms=N`.
const fs = require('node:fs')

const STT_TIMEOUT_MS = 60_000
const TTS_TIMEOUT_MS = 60_000
const TTS_MAX_TEXT_CHARS = 4000
const STT_MAX_AUDIO_BYTES = 24 * 1024 * 1024
const CONFIG_CACHE_TTL_MS = 10_000

// Per-process call counters. Each TTS/STT request gets a stable id
// ([tts#N] / [stt#N]) so the operator can follow one speak() from
// the routing decision through the final audio bytes.
let ttsSeq = 0
let sttSeq = 0

function decodeDataUrl(dataUrl) {
  // data:[<mime>][;base64],<payload>
  const match = /^data:([^;,]+)?(;base64)?,(.*)$/s.exec(String(dataUrl || ''))
  if (!match) throw new Error('Expected a base64 data URL')
  const mime = match[1] || 'application/octet-stream'
  const isBase64 = Boolean(match[2])
  const payload = match[3]
  const data = isBase64 ? Buffer.from(payload, 'base64') : Buffer.from(decodeURIComponent(payload), 'utf8')
  return { mime, data }
}

async function postMultipart({ url, token, form, timeoutMs }) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: form,
    signal: AbortSignal.timeout(timeoutMs)
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`${res.status} ${new URL(url).pathname}: ${text || res.statusText}`)
  }
  const buf = Buffer.from(await res.arrayBuffer())
  return { body: buf, contentType: res.headers.get('content-type') || '' }
}

// Local-availability probe: a Runner tool is usable iff the (check_fn-gated)
// schema list currently advertises it. `bridge` may be null or disconnected
// (e.g. during cold start), in which case nothing local is available.
function localToolAvailable(bridge, toolName) {
  if (!bridge) return false
  const tools = bridge.getTools()
  if (!Array.isArray(tools)) return false
  return tools.some(t => (t?.function?.name || t?.name) === toolName)
}

function formatKv(prefix, event, base, extra = {}) {
  const parts = []
  // Same filter rules for both ``base`` and ``extra``: drop nullish and
  // explicit false so the trace always reads as one self-contained line
  // with only meaningful fields (an omitted `is_designed=false` is more
  // useful than `is_designed=false` for tracing).
  const isOmitted = v => v === null || v === undefined || v === false
  for (const [k, v] of Object.entries(base)) {
    if (isOmitted(v)) continue
    parts.push(typeof v === 'string' ? `${k}=${JSON.stringify(v)}` : `${k}=${v}`)
  }
  for (const [k, v] of Object.entries(extra)) {
    if (isOmitted(v)) continue
    parts.push(typeof v === 'string' ? `${k}=${JSON.stringify(v)}` : `${k}=${v}`)
  }
  return `${prefix} ${event} ${parts.join(' ')}`
}

function makeTtsLog({ log, id, voice, enginePref, isDesigned, context }) {
  const base = { voice_in: voice || '', engine_pref: enginePref, context: context || null }
  if (isDesigned) base.is_designed = true
  return (event, extra = {}) => log(formatKv(`[tts#${id}]`, event, base, extra))
}

function makeSttLog({ log, id, mime, enginePref, context }) {
  const base = { engine_pref: enginePref, context: context || null }
  if (mime) base.mime = mime
  return (event, extra = {}) => log(formatKv(`[stt#${id}]`, event, base, extra))
}

// Run the local STT tool; never throws — returns {ok,value} or {ok:false,error}.
async function tryLocalStt({ bridge, mime, data }) {
  try {
    const result = await bridge.invoke('speech_to_text', {
      audio_base64: data.toString('base64'),
      mime_type: mime
    })
    if (result && result.success === true && typeof result.text === 'string') {
      return { ok: true, value: { text: result.text } }
    }
    const msg = result?.error ? String(result.error) : 'local STT returned no text'
    return { ok: false, error: new Error(msg) }
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e : new Error(String(e)) }
  }
}

// Run the local TTS tool and bridge its local-file output to a data URL.
// We never forward the caller's voice to Piper: cloud voice ids
// (e.g. "mimo_default", "冰糖") are meaningless to it, and even local Piper
// voice ids (e.g. "en_US-amy-medium") are not exposed through the companion
// UI.  Piper always uses its own default_voice from config.yaml
// (audio.tts.default_voice).  Users who need a specific Piper voice set it
// in Runner config, not through the companion picker.
async function tryLocalTts({ bridge, text }) {
  try {
    const result = await bridge.invoke('text_to_speech', { text })
    if (result && result.success === true && result.path) {
      const buf = fs.readFileSync(result.path)
      return {
        ok: true,
        value: {
          dataUrl: `data:audio/wav;base64,${buf.toString('base64')}`,
          mimeType: 'audio/wav',
          // Engine + voice come from the runner tool response (see
          // runner/tools/multimodal/audio/tts_tool.py) — surfaced to the
          // caller so the trace line can show the actual Piper/pyttsx3
          // voice used, which is NOT the cloud voice id the user picked.
          engine: result.engine || 'unknown',
          voice: result.voice || '(default)'
        }
      }
    }
    const msg = result?.error ? String(result.error) : 'local TTS returned no audio'
    return { ok: false, error: new Error(msg) }
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e : new Error(String(e)) }
  }
}

async function sttViaBackend({ ensureBackend, mime, data, filename, language }) {
  const connection = await ensureBackend()
  const form = new FormData()
  form.set('audio_file', new Blob([data], { type: mime }), filename)
  if (language) {
    form.set('language', language)
  }
  const { body } = await postMultipart({
    url: `${connection.baseUrl}/api/media/stt`,
    token: connection.token,
    form,
    timeoutMs: STT_TIMEOUT_MS
  })
  const parsed = JSON.parse(body.toString('utf8'))
  return { text: parsed.text || '' }
}

async function ttsViaBackend({ ensureBackend, text, voice }) {
  const connection = await ensureBackend()
  const form = new FormData()
  form.set('text', text)
  form.set('voice', voice)
  const { body, contentType } = await postMultipart({
    url: `${connection.baseUrl}/api/media/tts`,
    token: connection.token,
    form,
    timeoutMs: TTS_TIMEOUT_MS
  })
  const mime = contentType.split(';')[0].trim() || 'audio/mpeg'
  return { dataUrl: `data:${mime};base64,${body.toString('base64')}`, mimeType: mime, voiceOut: voice }
}

// Cached reader for `stt.engine` / `tts.engine` from GET /api/config. Short TTL
// keeps each request off the backend while bounding staleness after a settings
// change. On any fetch failure we default to "auto" (local-first) so media
// still works when the backend is unreachable — local engines need no cloud.
function createEnginePrefsCache({ ensureBackend, ttlMs = CONFIG_CACHE_TTL_MS }) {
  let cached = null
  return async function getEnginePrefs() {
    const now = Date.now()
    if (cached && cached.expiresAt > now) return cached
    try {
      const connection = await ensureBackend()
      const res = await fetch(`${connection.baseUrl}/api/config`, {
        headers: { ...(connection.token ? { Authorization: `Bearer ${connection.token}` } : {}) },
        signal: AbortSignal.timeout(10_000)
      })
      if (!res.ok) throw new Error(`${res.status} /api/config`)
      const body = await res.json()
      const config = body?.config || {}
      cached = {
        stt: config.stt?.engine || 'auto',
        tts: config.tts?.engine || 'auto',
        expiresAt: now + ttlMs
      }
    } catch {
      cached = { stt: 'auto', tts: 'auto', expiresAt: now + ttlMs }
    }
    return cached
  }
}

// Backend media proxy (STT/TTS). Kept separate from ipc/connection.cjs
// because the generic deskagent:api proxy only ships JSON and audio endpoints
// need multipart upload (STT) and binary download (TTS).
//
// `log` is the dev-terminal trace sink. Optional so unit tests can skip it.
function registerMediaIpc({ ipcMain, ensureBackend, getRunnerBridge, getEnginePrefs, log = () => {} }) {
  const resolvePrefs = typeof getEnginePrefs === 'function' ? getEnginePrefs : createEnginePrefsCache({ ensureBackend })
  const bridge = () => (typeof getRunnerBridge === 'function' ? getRunnerBridge() : null)

  ipcMain.handle('deskagent:media:stt', async (_event, payload) => {
    const sttId = ++sttSeq
    const { mime, data } = decodeDataUrl(payload?.dataUrl)
    if (data.length > STT_MAX_AUDIO_BYTES) {
      throw new Error(`Audio too large (${data.length} bytes; max ${STT_MAX_AUDIO_BYTES})`)
    }
    const filename = payload?.filename || `recording.${(mime.split('/')[1] || 'webm').split(';')[0]}`
    const context = typeof payload?.context === 'string' ? payload.context : null

    // Product direction is "default Chinese" for TTS/STT (per
    // CLAUDE.md / product brief). Renderer's `media.stt` IPC doesn't
    // currently pass a `language` arg, so we hint Whisper with `zh` to
    // improve accuracy on the common case (Chinese chat voice input).
    // Operators who want auto-detect can pass `language: 'auto'` from
    // the renderer explicitly.
    const language = (typeof payload?.language === 'string' && payload.language) ? payload.language : 'zh'

    const prefs = await resolvePrefs()
    const engine = prefs.stt
    const sttLog = makeSttLog({ log, id: sttId, mime, enginePref: engine, context })
    const startedAt = Date.now()

    if (engine !== 'cloud') {
      if (localToolAvailable(bridge(), 'speech_to_text')) {
        const res = await tryLocalStt({ bridge: bridge(), mime, data })
        if (res.ok) {
          sttLog('done', { route: 'local', text_chars: res.value.text.length, ms: Date.now() - startedAt })
          return res.value
        }
        if (engine === 'local') {
          sttLog('done', { route: 'local', error: res.error.message, ms: Date.now() - startedAt })
          throw res.error
        }
        sttLog('fallback', { from: 'local', to: 'cloud', reason: res.error.message })
      } else if (engine === 'local') {
        sttLog('done', { route: 'local', error: 'Local STT unavailable', ms: Date.now() - startedAt })
        throw new Error('Local STT unavailable: runner not connected or speech_to_text tool missing')
      }
    }

    const result = await sttViaBackend({ ensureBackend, mime, data, filename, language })
    sttLog('done', { route: 'cloud', text_chars: result.text.length, language, ms: Date.now() - startedAt })
    return result
  })

  ipcMain.handle('deskagent:media:tts', async (_event, payload) => {
    const ttsId = ++ttsSeq
    const text = String(payload?.text || '')
    if (!text) throw new Error('text is required')
    if (text.length > TTS_MAX_TEXT_CHARS) {
      throw new Error(`text exceeds ${TTS_MAX_TEXT_CHARS} chars`)
    }
    const voice = String(payload?.voice || '')
    const context = typeof payload?.context === 'string' ? payload.context : null

    const prefs = await resolvePrefs()
    // Designed voices are encoded as ``mimo_voicedesign:<prompt>`` tokens
    // (see MiMoTTSProvider.synthesize). The local Piper engine has no
    // notion of these — even under ``tts.engine='auto'`` we must route to
    // the cloud backend or the user pays for a voicedesign call and hears
    // Piper's default voice instead.
    const isDesigned = voice.startsWith('mimo_voicedesign:')
    const engine = isDesigned ? 'cloud' : prefs.tts

    const ttsLog = makeTtsLog({ log, id: ttsId, voice, enginePref: engine, isDesigned, context })
    const startedAt = Date.now()

    if (engine !== 'cloud') {
      if (localToolAvailable(bridge(), 'text_to_speech')) {
        const res = await tryLocalTts({ bridge: bridge(), text })
        if (res.ok) {
          ttsLog('done', {
            route: 'local',
            engine: res.value.engine,
            voice: res.value.voice,
            mime: res.value.mimeType,
            ms: Date.now() - startedAt
          })
          return { dataUrl: res.value.dataUrl, mimeType: res.value.mimeType }
        }
        if (engine === 'local') {
          ttsLog('done', { route: 'local', error: res.error.message, ms: Date.now() - startedAt })
          throw res.error
        }
        ttsLog('fallback', { from: 'local', to: 'cloud', reason: res.error.message })
      } else if (engine === 'local') {
        ttsLog('done', { route: 'local', error: 'Local TTS unavailable', ms: Date.now() - startedAt })
        throw new Error('Local TTS unavailable: runner not connected or text_to_speech tool missing')
      }
    }

    const result = await ttsViaBackend({ ensureBackend, text, voice })
    ttsLog('done', {
      route: 'cloud',
      voice_out: result.voiceOut || null,
      mime: result.mimeType,
      ms: Date.now() - startedAt
    })
    return { dataUrl: result.dataUrl, mimeType: result.mimeType }
  })
}

module.exports = { registerMediaIpc, createEnginePrefsCache }
