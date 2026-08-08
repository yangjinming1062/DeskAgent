'use strict'

const path = require('node:path')

const MEDIA_MIME_TYPES = {
  '.avi': 'video/x-msvideo',
  '.bmp': 'image/bmp',
  '.flac': 'audio/flac',
  '.gif': 'image/gif',
  '.jpeg': 'image/jpeg',
  '.jpg': 'image/jpeg',
  '.m4a': 'audio/mp4',
  '.mkv': 'video/x-matroska',
  '.mov': 'video/quicktime',
  '.mp3': 'audio/mpeg',
  '.mp4': 'video/mp4',
  '.ogg': 'audio/ogg',
  '.opus': 'audio/ogg; codecs=opus',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.wav': 'audio/wav',
  '.webm': 'video/webm',
  '.webp': 'image/webp'
}

// Derived: any ext whose MIME starts with audio/ or video/.
const STREAMABLE_MEDIA_EXTS = new Set(
  Object.entries(MEDIA_MIME_TYPES)
    .filter(([, mime]) => mime.startsWith('audio/') || mime.startsWith('video/'))
    .map(([ext]) => ext)
)

function mimeTypeForPath(filePath) {
  const ext = path.extname(filePath || '').toLowerCase()
  return MEDIA_MIME_TYPES[ext] || 'application/octet-stream'
}

function extensionForMimeType(mimeType) {
  const type = String(mimeType || '')
    .split(';')[0]
    .trim()
    .toLowerCase()
  if (type === 'image/png') return '.png'
  if (type === 'image/jpeg') return '.jpg'
  if (type === 'image/gif') return '.gif'
  if (type === 'image/webp') return '.webp'
  if (type === 'image/bmp') return '.bmp'
  if (type === 'image/svg+xml') return '.svg'
  return ''
}

function dataUrlFromBuffer(buffer, mimeType) {
  return `data:${mimeType};base64,${buffer.toString('base64')}`
}

// Decode a `data:<mime>[;base64],<payload>` URL back to the raw bytes.
// Single source of truth shared by media.cjs (STT decode) and
// reaction-audio.cjs (TTS response → mp3 disk write).
function dataUrlToBuffer(dataUrl) {
  const match = /^data:([^;,]+)?(;base64)?,(.*)$/s.exec(String(dataUrl || ''))
  if (!match) throw new Error('Expected a base64 data URL')
  const isBase64 = Boolean(match[2])
  const payload = match[3]
  return isBase64 ? Buffer.from(payload, 'base64') : Buffer.from(decodeURIComponent(payload), 'utf8')
}

module.exports = {
  MEDIA_MIME_TYPES,
  STREAMABLE_MEDIA_EXTS,
  mimeTypeForPath,
  extensionForMimeType,
  dataUrlFromBuffer,
  dataUrlToBuffer
}
