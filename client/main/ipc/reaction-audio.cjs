'use strict'

const fsp = require('node:fs').promises
const path = require('node:path')

const { atomicWriteFile } = require('../shared/utils.cjs')
const { dataUrlFromBuffer, dataUrlToBuffer } = require('../shared/mime.cjs')

// Held as a module reference (not destructured) so test code can monkey-patch
// `media.ttsViaBackend` and have the next call observe the swap. Real-world
// production never reassigns the export.
const media = require('./media.cjs')

const ID_RE = /^reaction\.[a-z0-9_.-]+$/
// ~10KB expected per reaction clip; 64KB still leaves headroom for wide-form
// voices and longer poke lines without letting a stray large file blow up the
// IPC payload.
const MAX_BYTES = 64 * 1024

const KNOWN_BUCKETS = new Set(['poke-light', 'poke-medium', 'poke-heavy', 'drag'])

function isValidEntry(entry) {
  if (!entry || typeof entry !== 'object') return false
  const { id, text, bucket, tags } = entry
  if (typeof id !== 'string' || !ID_RE.test(id)) return false
  if (typeof text !== 'string' || !text.trim()) return false
  if (!KNOWN_BUCKETS.has(bucket)) return false
  if (tags !== undefined && !Array.isArray(tags)) return false
  return true
}

function registerReactionAudioIpc({ ipcMain, deskagentHome, mimeTypeForPath, hardening, ensureBackend }) {
  if (!hardening) throw new Error('registerReactionAudioIpc: hardening is required')
  if (!deskagentHome) throw new Error('registerReactionAudioIpc: deskagentHome is required')
  if (typeof mimeTypeForPath !== 'function') {
    throw new Error('registerReactionAudioIpc: mimeTypeForPath is required')
  }
  if (typeof ensureBackend !== 'function') {
    throw new Error('registerReactionAudioIpc: ensureBackend is required')
  }

  const audioRoot = path.resolve(deskagentHome, 'audio', 'reactions', 'zh')

  ipcMain.handle('deskagent:reactionAudio:read', async (_event, id) => {
    if (typeof id !== 'string' || !ID_RE.test(id)) {
      throw new Error(`invalid reaction audio id: ${id}`)
    }

    const targetPath = path.join(audioRoot, `${id}.mp3`)

    const { resolvedPath } = await hardening.resolveReadableFileForIpc(targetPath, {
      maxBytes: MAX_BYTES,
      purpose: 'Reaction audio'
    })
    const data = await fsp.readFile(resolvedPath)
    const mimeType = mimeTypeForPath(resolvedPath)
    return { dataUrl: dataUrlFromBuffer(data, mimeType), mimeType, id, bytes: data.length }
  })

  ipcMain.handle('deskagent:reactionAudio:generate', async (_event, payload) => {
    if (!payload || typeof payload !== 'object') {
      throw new Error('payload is required')
    }
    const voice = String(payload.voice || '').trim()
    const language = String(payload.language || 'zh').trim() || 'zh'
    const entries = Array.isArray(payload.entries) ? payload.entries : []

    if (!voice) {
      throw new Error('voice is required')
    }
    if (entries.length === 0) {
      return { results: [] }
    }

    // Per-entry validation. Id regex alone is not enough — bucket must come from
    // a known pair (defends against renderer bugs / typos).
    const valid = entries.filter(isValidEntry)

    await fsp.mkdir(audioRoot, { recursive: true })

    // Cloud-only path (Piper quality is poor); throttle is enforced inside
    // ttsViaBackend so this module no longer needs its own per-entry delay.
    const results = []
    for (const entry of valid) {
      try {
        const { dataUrl } = await media.ttsViaBackend({ ensureBackend, text: entry.text, voice, language })
        const buf = dataUrlToBuffer(dataUrl)
        await atomicWriteFile(path.join(audioRoot, `${entry.id}.mp3`), buf)
        results.push({ id: entry.id, ok: true, bytes: buf.length })
      } catch (err) {
        const reason = err instanceof Error ? err.message : String(err)
        results.push({ id: entry.id, ok: false, reason })
      }
    }

    const failed = results.filter(r => !r.ok).length
    if (failed > 0) {
      console.warn(`[reaction-audio] batch: ${results.length - failed}/${results.length} ok, ${failed} failed`)
    }
    return { results }
  })
}

module.exports = { registerReactionAudioIpc, isValidEntry }
