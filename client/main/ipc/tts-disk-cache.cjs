'use strict'

const crypto = require('node:crypto')
const fsp = require('node:fs').promises
const path = require('node:path')

const { atomicWriteFile } = require('../shared/utils.cjs')

// Persistent cache for *scripted* TTS lines — poke/drag reactions and voice
// preview samples, whose text is fixed in the source tree. Dynamic speech
// (chat replies, proactive messages, voice calls) is never persisted; it lives
// only in the in-memory LRU in media.cjs.
//
// Entries are content-addressed by sha1(voice + "\n" + text), so switching
// voice or editing a line's text changes the key and misses naturally. There is
// no invalidation logic to keep in sync with the manifest.
//
// Three rules govern the cache — the second and third are enforced by the
// caller in media.cjs, which is the only place that knows the routing outcome:
//   read      a hit is served whatever `tts.engine` says. Those bytes were
//             already paid for and carry the voice the user picked.
//   generate  always follows the user's `tts.engine` preference.
//   write     cloud results only. Persisting Piper output would let it
//             impersonate the chosen cloud voice for good.
const MAX_CACHE_FILES = 300
const MAX_ENTRY_BYTES = 256 * 1024

function cacheKey(voice, text) {
  return crypto.createHash('sha1').update(`${voice}\n${text}`).digest('hex')
}

function createTtsDiskCache({ deskagentHome }) {
  if (!deskagentHome) throw new Error('createTtsDiskCache: deskagentHome is required')

  const dirFor = language => path.resolve(deskagentHome, 'audio', 'tts-cache', language)
  const pathFor = (voice, text, language) => path.join(dirFor(language), `${cacheKey(voice, text)}.mp3`)

  // Oldest-first eviction. Scripted lines are a small fixed pool per voice, so
  // the newest MAX_CACHE_FILES entries always cover the voice in active use.
  async function sweep(language) {
    try {
      const dir = dirFor(language)
      const names = await fsp.readdir(dir)
      if (names.length <= MAX_CACHE_FILES) return

      const entries = await Promise.all(
        names.map(async name => {
          const filePath = path.join(dir, name)
          const stat = await fsp.stat(filePath).catch(() => null)
          return stat?.isFile() ? { filePath, mtimeMs: stat.mtimeMs } : null
        })
      )
      const files = entries.filter(Boolean).sort((a, b) => a.mtimeMs - b.mtimeMs)

      for (const { filePath } of files.slice(0, files.length - MAX_CACHE_FILES)) {
        await fsp.unlink(filePath).catch(() => {})
      }
    } catch (err) {
      console.warn('[tts-disk-cache] sweep failed', err)
    }
  }

  return {
    // Buffer on hit, null on miss. Never throws — a miss just costs a synthesis.
    async read({ voice, text, language }) {
      try {
        const stat = await fsp.stat(pathFor(voice, text, language))
        // An oversized entry reads as a miss so the next call replaces it.
        if (!stat.isFile() || stat.size > MAX_ENTRY_BYTES) return null
        return await fsp.readFile(pathFor(voice, text, language))
      } catch {
        return null
      }
    },

    // Best-effort: a cache that fails to persist must not fail the playback
    // that produced the bytes.
    async write({ voice, text, language, buffer, mimeType }) {
      // The `.mp3` extension has to stay honest — every cloud provider returns
      // audio/mpeg today, so anything else means an assumption broke.
      if (mimeType !== 'audio/mpeg' || buffer.length > MAX_ENTRY_BYTES) return

      try {
        await atomicWriteFile(pathFor(voice, text, language), buffer)
        await sweep(language)
      } catch (err) {
        console.warn('[tts-disk-cache] write failed', err)
      }
    }
  }
}

module.exports = { createTtsDiskCache, cacheKey, MAX_CACHE_FILES, MAX_ENTRY_BYTES }
