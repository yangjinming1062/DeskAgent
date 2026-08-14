'use strict'

const crypto = require('node:crypto')
const fs = require('node:fs')
const fsp = require('node:fs').promises
const path = require('node:path')
const { Readable } = require('node:stream')
const { pipeline } = require('node:stream/promises')

const MAX_CACHE_FILES = 20
const MAX_CACHE_BYTES = 1024 * 1024 * 1024 // 1 GB
const DEFAULT_INACTIVITY_TIMEOUT_MS = 30_000

function computeFileSha256(filePath) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash('sha256')
    const stream = fs.createReadStream(filePath)
    stream.on('data', chunk => hash.update(chunk))
    stream.on('end', () => resolve(hash.digest('hex')))
    stream.on('error', reject)
  })
}

function createModelDiskCache({ deskagentHome, inactivityTimeoutMs = DEFAULT_INACTIVITY_TIMEOUT_MS }) {
  if (!deskagentHome) throw new Error('createModelDiskCache: deskagentHome is required')

  const cacheDir = path.resolve(deskagentHome, 'cache', 'models')
  const inFlightDownloads = new Map()

  async function ensureDir() {
    await fsp.mkdir(cacheDir, { recursive: true })
  }

  function getGlbPath(hash) {
    return path.join(cacheDir, `${hash}.glb`)
  }

  function getPartialPath(hash) {
    return path.join(cacheDir, `${hash}.partial`)
  }

  async function has(hash) {
    if (!hash || typeof hash !== 'string') return false
    try {
      const stat = await fsp.stat(getGlbPath(hash))
      return stat.isFile() && stat.size > 0
    } catch {
      return false
    }
  }

  async function getPath(hash) {
    if (await has(hash)) {
      return getGlbPath(hash)
    }
    return null
  }

  async function readBuffer(hash) {
    const glbPath = await getPath(hash)
    if (!glbPath) return null
    try {
      return await fsp.readFile(glbPath)
    } catch {
      return null
    }
  }

  async function sweep() {
    try {
      await ensureDir()
      const names = await fsp.readdir(cacheDir)
      const glbFiles = names.filter(n => n.endsWith('.glb'))

      const entries = await Promise.all(
        glbFiles.map(async name => {
          const filePath = path.join(cacheDir, name)
          const stat = await fsp.stat(filePath).catch(() => null)
          return stat?.isFile() ? { filePath, size: stat.size, mtimeMs: stat.mtimeMs } : null
        })
      )

      const files = entries.filter(Boolean).sort((a, b) => a.mtimeMs - b.mtimeMs)

      let totalBytes = files.reduce((acc, f) => acc + f.size, 0)
      let count = files.length

      for (const file of files) {
        if (count <= MAX_CACHE_FILES && totalBytes <= MAX_CACHE_BYTES) {
          break
        }
        await fsp.unlink(file.filePath).catch(() => {})
        totalBytes -= file.size
        count -= 1
      }
    } catch (err) {
      console.warn('[model-disk-cache] sweep failed', err)
    }
  }

  async function _download({ url, contentHash, token, baseUrl, fetchFn = globalThis.fetch }) {
    await ensureDir()

    const raw = String(url || '')
    if (!raw) throw new Error('url is required')

    // 1. If contentHash is known and exists in cache, return immediately
    if (contentHash && (await has(contentHash))) {
      const glbPath = getGlbPath(contentHash)
      fsp.utimes(glbPath, new Date(), new Date()).catch(() => {})
      return { filePath: glbPath, contentHash, fromCache: true }
    }

    const { pathname, search } = new URL(raw, baseUrl || 'http://127.0.0.1:8000')
    const targetUrl = baseUrl ? `${baseUrl}${pathname}${search}` : raw

    const downloadKey = contentHash || crypto.createHash('sha1').update(pathname).digest('hex')
    const partialPath = getPartialPath(downloadKey)

    async function attemptDownload(resumeFromBytes = 0) {
      const headers = {}
      if (token) {
        headers['Authorization'] = `Bearer ${token}`
      }
      if (resumeFromBytes > 0) {
        headers['Range'] = `bytes=${resumeFromBytes}-`
      }

      const controller = new AbortController()
      let timer = null

      function resetInactivityTimer() {
        if (timer) clearTimeout(timer)
        timer = setTimeout(() => {
          controller.abort(new Error(`Download stalled: no data received for ${inactivityTimeoutMs / 1000}s`))
        }, inactivityTimeoutMs)
      }

      resetInactivityTimer()

      let res
      try {
        res = await fetchFn(targetUrl, {
          headers,
          signal: controller.signal
        })
      } catch (err) {
        if (timer) clearTimeout(timer)
        throw err
      }

      if (!res.ok && res.status !== 206) {
        if (timer) clearTimeout(timer)
        if (res.status === 416) {
          await fsp.unlink(partialPath).catch(() => {})
          const error = new Error('416 Range Not Satisfiable')
          error.status = 416
          throw error
        }
        const text = await res.text().catch(() => '')
        throw new Error(`${res.status} ${pathname}: ${text || res.statusText}`)
      }

      const isPartial = res.status === 206
      const writeStream = fs.createWriteStream(partialPath, {
        flags: isPartial && resumeFromBytes > 0 ? 'a' : 'w'
      })

      const bodyStream = res.body
      let readableNodeStream
      if (bodyStream && typeof bodyStream.getReader === 'function') {
        readableNodeStream = Readable.fromWeb(bodyStream)
      } else if (bodyStream && typeof bodyStream[Symbol.asyncIterator] === 'function') {
        readableNodeStream = Readable.from(bodyStream)
      } else {
        const arrayBuf = await res.arrayBuffer()
        readableNodeStream = Readable.from(Buffer.from(arrayBuf))
      }

      readableNodeStream.on('data', () => {
        resetInactivityTimer()
      })

      try {
        await pipeline(readableNodeStream, writeStream)
      } finally {
        if (timer) clearTimeout(timer)
      }

      const rawEtag = typeof res.headers?.get === 'function' ? res.headers.get('etag') : res.headers?.['etag']
      const rawSha =
        typeof res.headers?.get === 'function' ? res.headers.get('x-content-sha256') : res.headers?.['x-content-sha256']
      const headerSha = rawSha || rawEtag?.replace(/"/g, '')
      return headerSha || null
    }

    let stat = await fsp.stat(partialPath).catch(() => null)
    let resumeBytes = stat?.isFile() ? stat.size : 0

    let headerSha = null
    try {
      headerSha = await attemptDownload(resumeBytes)
    } catch (err) {
      if (err?.status === 416) {
        headerSha = await attemptDownload(0)
      } else {
        throw err
      }
    }

    const finalHash = await computeFileSha256(partialPath)

    if (contentHash && finalHash !== contentHash) {
      await fsp.unlink(partialPath).catch(() => {})
      throw new Error(`Model hash mismatch: expected ${contentHash}, got ${finalHash}`)
    }

    const resolvedHash = contentHash || headerSha || finalHash
    const finalGlbPath = getGlbPath(resolvedHash)

    await fsp.rename(partialPath, finalGlbPath)
    sweep().catch(() => {})

    return {
      filePath: finalGlbPath,
      contentHash: resolvedHash,
      fromCache: false
    }
  }

  /**
   * Stream download with Range resumption, inactivity timeout, and in-flight deduplication.
   */
  async function ensureCached(opts) {
    const raw = String(opts?.url || '')
    const key = `${opts?.contentHash || ''}:${raw}`
    if (inFlightDownloads.has(key)) {
      return await inFlightDownloads.get(key)
    }

    const promise = _download(opts).finally(() => {
      inFlightDownloads.delete(key)
    })
    inFlightDownloads.set(key, promise)
    return await promise
  }

  return {
    has,
    getPath,
    readBuffer,
    ensureCached,
    sweep,
    getGlbPath,
    getPartialPath
  }
}

module.exports = {
  createModelDiskCache,
  computeFileSha256,
  MAX_CACHE_FILES,
  MAX_CACHE_BYTES,
  DEFAULT_INACTIVITY_TIMEOUT_MS
}
