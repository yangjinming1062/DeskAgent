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

// Inverse of MEDIA_MIME_TYPES for image/*.
const IMAGE_MIME_EXTENSIONS = new Set([
  'image/png',
  'image/jpeg',
  'image/gif',
  'image/webp',
  'image/bmp',
  'image/svg+xml'
])
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

module.exports = {
  MEDIA_MIME_TYPES,
  STREAMABLE_MEDIA_EXTS,
  IMAGE_MIME_EXTENSIONS,
  mimeTypeForPath,
  extensionForMimeType
}
