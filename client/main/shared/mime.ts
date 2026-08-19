import path from 'node:path'

export const MEDIA_MIME_TYPES: Record<string, string> = {
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

// 派生：MIME 以 audio/ 或 video/ 开头的所有扩展名。
export const STREAMABLE_MEDIA_EXTS: Set<string> = new Set(
  Object.entries(MEDIA_MIME_TYPES)
    .filter(([, mime]) => mime.startsWith('audio/') || mime.startsWith('video/'))
    .map(([ext]) => ext)
)

export function mimeTypeForPath(filePath: string): string {
  const ext = path.extname(filePath || '').toLowerCase()

  return MEDIA_MIME_TYPES[ext] || 'application/octet-stream'
}

export function extensionForMimeType(mimeType: string): string {
  const type = String(mimeType || '')
    .split(';')[0]
    .trim()
    .toLowerCase()

  if (type === 'image/png') {
    return '.png'
  }

  if (type === 'image/jpeg') {
    return '.jpg'
  }

  if (type === 'image/gif') {
    return '.gif'
  }

  if (type === 'image/webp') {
    return '.webp'
  }

  if (type === 'image/bmp') {
    return '.bmp'
  }

  if (type === 'image/svg+xml') {
    return '.svg'
  }

  return ''
}

export function dataUrlFromBuffer(buffer: Buffer | Uint8Array, mimeType: string): string {
  const buf = Buffer.isBuffer(buffer) ? buffer : Buffer.from(buffer)

  return `data:${mimeType};base64,${buf.toString('base64')}`
}

// 将 `data:<mime>[;base64],<payload>` 形式的 URL 解码回原始字节。
export function dataUrlToBuffer(dataUrl: string): Buffer {
  const match = /^data:([^;,]+)?(;base64)?,(.*)$/s.exec(String(dataUrl || ''))

  if (!match) {
    throw new Error('Expected a base64 data URL')
  }

  const isBase64 = Boolean(match[2])
  const payload = match[3]

  return isBase64 ? Buffer.from(payload, 'base64') : Buffer.from(decodeURIComponent(payload), 'utf8')
}
