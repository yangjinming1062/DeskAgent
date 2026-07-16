'use strict'

const STT_TIMEOUT_MS = 60_000
const TTS_TIMEOUT_MS = 60_000
const TTS_MAX_TEXT_CHARS = 4000
const STT_MAX_AUDIO_BYTES = 24 * 1024 * 1024

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

// Backend media proxy (STT/TTS). Kept separate from ipc/connection.cjs
// because the generic zast:api proxy only ships JSON and audio endpoints
// need multipart upload (STT) and binary download (TTS).
function registerMediaIpc({ ipcMain, ensureBackend }) {
  ipcMain.handle('zast:media:stt', async (_event, payload) => {
    const connection = await ensureBackend()
    const { mime, data } = decodeDataUrl(payload?.dataUrl)
    if (data.length > STT_MAX_AUDIO_BYTES) {
      throw new Error(`Audio too large (${data.length} bytes; max ${STT_MAX_AUDIO_BYTES})`)
    }
    const filename = payload?.filename || `recording.${(mime.split('/')[1] || 'webm').split(';')[0]}`
    const form = new FormData()
    form.set('audio_file', new Blob([data], { type: mime }), filename)
    const { body } = await postMultipart({
      url: `${connection.baseUrl}/api/media/stt`,
      token: connection.token,
      form,
      timeoutMs: STT_TIMEOUT_MS
    })
    const parsed = JSON.parse(body.toString('utf8'))
    return { text: parsed.text || '' }
  })

  ipcMain.handle('zast:media:tts', async (_event, payload) => {
    const text = String(payload?.text || '')
    if (!text) throw new Error('text is required')
    if (text.length > TTS_MAX_TEXT_CHARS) {
      throw new Error(`text exceeds ${TTS_MAX_TEXT_CHARS} chars`)
    }
    const voice = String(payload?.voice || 'alloy')
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
    return { dataUrl: `data:${mime};base64,${body.toString('base64')}`, mimeType: mime }
  })
}

module.exports = { registerMediaIpc }
