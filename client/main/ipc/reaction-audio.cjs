'use strict'

const fsp = require('node:fs').promises
const path = require('node:path')

const { atomicWriteFile } = require('../shared/utils.cjs')
const { dataUrlFromBuffer, dataUrlToBuffer } = require('../shared/mime.cjs')

// Held as a module reference (not destructured) so test code can monkey-patch
// `media.ttsViaBackend` and have the next call observe the swap. Real-world
// production never reassigns the export.
const media = require('./media.cjs')

const TAG_RE = /^reaction\.[a-z0-9-]+\.(gentle|lively|snarky|calm)\.[0-9]+$/
// ~10KB expected per reaction clip; 64KB still leaves headroom for wide-form
// voices and longer poke lines without letting a stray large file blow up the
// IPC payload.
const MAX_BYTES = 64 * 1024

// Concurrent fan-out for the batch generator. Matches
// assets/onboarding-audio/generate_onboarding_audio.py:23 so an upstream
// throttle hitting one consumer hits both consistently.
const BATCH_CONCURRENCY = 10

const KNOWN_TONES = new Set(['gentle', 'lively', 'snarky', 'calm'])
const KNOWN_BUCKETS = new Set(['poke-light', 'poke-medium', 'poke-heavy', 'drag'])

function isValidEntry(entry) {
  if (!entry || typeof entry !== 'object') return false
  const { tag, text, tone, bucket } = entry
  if (typeof tag !== 'string' || !TAG_RE.test(tag)) return false
  if (typeof text !== 'string' || !text.trim()) return false
  if (!KNOWN_TONES.has(tone)) return false
  if (!KNOWN_BUCKETS.has(bucket)) return false
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

  ipcMain.handle('deskagent:reactionAudio:read', async (_event, tag) => {
    if (typeof tag !== 'string' || !TAG_RE.test(tag)) {
      throw new Error(`invalid reaction audio tag: ${tag}`)
    }

    const targetPath = path.join(audioRoot, `${tag}.mp3`)

    const { resolvedPath } = await hardening.resolveReadableFileForIpc(targetPath, {
      maxBytes: MAX_BYTES,
      purpose: 'Reaction audio'
    })
    const data = await fsp.readFile(resolvedPath)
    const mimeType = mimeTypeForPath(resolvedPath)
    return { dataUrl: dataUrlFromBuffer(data, mimeType), mimeType, tag, bytes: data.length }
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

    // Per-entry validation. Tag regex alone is not enough — bucket and tone
    // must come from a known pair (defends against renderer bugs / typos).
    const valid = entries.filter(isValidEntry)

    await fsp.mkdir(audioRoot, { recursive: true })

    // Sequential-runnable concurrency control. A simple `Promise.all` of all
    // tasks would happily fire hundreds of MiMo requests at once; cap to the
    // same fan-out the offline generator uses.
    const sem = { active: 0, max: BATCH_CONCURRENCY, waiters: [] }
    const acquire = () =>
      new Promise(resolve => {
        const tryAcquire = () => {
          if (sem.active < sem.max) {
            sem.active += 1
            resolve(null)
          } else {
            sem.waiters.push(tryAcquire)
          }
        }
        tryAcquire()
      })
    const release = () => {
      sem.active -= 1
      const next = sem.waiters.shift()
      if (next) next()
    }

    async function process(entry) {
      await acquire()
      try {
        // Cloud-only path. We deliberately skip the local Piper TTS engine
        // even under the caller's `'auto'` preference because Piper voice
        // quality is poor — the user's whole reason for letting us bake
        // reactions in the first place is that they want their chosen voice.
        // The cloud backend still respects `tts.engine = local` for users who
        // explicitly opted into it (e.g. privacy), but for baking reactions in
        // bulk this path is intentionally cloud-only.
        const { dataUrl } = await media.ttsViaBackend({ ensureBackend, text: entry.text, voice, language })
        const buf = dataUrlToBuffer(dataUrl)
        await atomicWriteFile(path.join(audioRoot, `${entry.tag}.mp3`), buf)
        return { tag: entry.tag, ok: true, bytes: buf.length }
      } catch (err) {
        const reason = err instanceof Error ? err.message : String(err)
        return { tag: entry.tag, ok: false, reason }
      } finally {
        release()
      }
    }

    const settled = await Promise.all(valid.map(process))
    const failed = settled.filter(r => !r.ok).length
    if (failed > 0) {
      console.warn(`[reaction-audio] batch: ${settled.length - failed}/${settled.length} ok, ${failed} failed`)
    }
    return { results: settled }
  })
}

module.exports = { registerReactionAudioIpc, isValidEntry }
