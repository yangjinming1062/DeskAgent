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

module.exports = {
  MEDIA_MIME_TYPES,
  STREAMABLE_MEDIA_EXTS,
  mimeTypeForPath,
  extensionForMimeType,
  dataUrlFromBuffer
}
