import crypto from 'node:crypto'
import fs from 'node:fs'
import fsp from 'node:fs/promises'
import path from 'node:path'
import { Readable } from 'node:stream'
import { pipeline } from 'node:stream/promises'

export const MAX_CACHE_FILES = 20
export const MAX_CACHE_BYTES = 1024 * 1024 * 1024 // 1 GB
export const DEFAULT_INACTIVITY_TIMEOUT_MS = 30_000

export function computeFileSha256(filePath: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash('sha256')
    const stream = fs.createReadStream(filePath)
    stream.on('data', chunk => hash.update(chunk))
    stream.on('end', () => resolve(hash.digest('hex')))
    stream.on('error', reject)
  })
}

export interface ModelDiskCacheOptions {
  deskagentHome?: null | string
  inactivityTimeoutMs?: number
}

export interface EnsureCachedOptions {
  baseUrl?: string
  contentHash?: string
  fetchFn?: typeof globalThis.fetch
  token?: string
  url: string
}

export interface ModelDiskCache {
  ensureCached: (opts: EnsureCachedOptions) => Promise<{ contentHash: string; filePath: string; fromCache: boolean }>
  getGlbPath: (hash: string) => string
  getPartialPath: (hash: string) => string
  getPath: (hash: string) => Promise<null | string>
  has: (hash: string) => Promise<boolean>
  readBuffer: (hash: string) => Promise<Buffer | null>
  sweep: () => Promise<void>
}

export function createModelDiskCache({
  deskagentHome,
  inactivityTimeoutMs = DEFAULT_INACTIVITY_TIMEOUT_MS
}: ModelDiskCacheOptions): ModelDiskCache {
  if (!deskagentHome) {
    throw new Error('createModelDiskCache: deskagentHome is required')
  }

  const cacheDir = path.resolve(deskagentHome, 'cache', 'models')
  const inFlightDownloads = new Map<string, Promise<{ contentHash: string; filePath: string; fromCache: boolean }>>()

  async function ensureDir(): Promise<void> {
    await fsp.mkdir(cacheDir, { recursive: true })
  }

  function getGlbPath(hash: string): string {
    return path.join(cacheDir, `${hash}.glb`)
  }

  function getPartialPath(hash: string): string {
    return path.join(cacheDir, `${hash}.partial`)
  }

  async function has(hash?: string | null): Promise<boolean> {
    if (!hash || typeof hash !== 'string') {
      return false
    }

    try {
      const stat = await fsp.stat(getGlbPath(hash))

      return stat.isFile() && stat.size > 0
    } catch {
      return false
    }
  }

  async function getPath(hash?: string | null): Promise<null | string> {
    if (await has(hash)) {
      return getGlbPath(hash!)
    }

    return null
  }

  async function readBuffer(hash?: string | null): Promise<Buffer | null> {
    const glbPath = await getPath(hash)

    if (!glbPath) {
      return null
    }

    try {
      return await fsp.readFile(glbPath)
    } catch {
      return null
    }
  }

  async function sweep(): Promise<void> {
    try {
      await ensureDir()
      const names = await fsp.readdir(cacheDir)
      const glbFiles = names.filter(n => n.endsWith('.glb'))

      const entries = await Promise.all(
        glbFiles.map(async name => {
          const filePath = path.join(cacheDir, name)
          const stat = await fsp.stat(filePath).catch(() => null)

          return stat?.isFile() ? { filePath, mtimeMs: stat.mtimeMs, size: stat.size } : null
        })
      )

      const files = entries.filter((e): e is NonNullable<typeof e> => e !== null).sort((a, b) => a.mtimeMs - b.mtimeMs)

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

  async function _download({
    baseUrl,
    contentHash,
    fetchFn = globalThis.fetch,
    token,
    url
  }: EnsureCachedOptions): Promise<{ contentHash: string; filePath: string; fromCache: boolean }> {
    await ensureDir()

    const raw = String(url || '')

    if (!raw) {
      throw new Error('url is required')
    }

    if (contentHash && (await has(contentHash))) {
      const glbPath = getGlbPath(contentHash)
      fsp.utimes(glbPath, new Date(), new Date()).catch(() => {})

      return { contentHash, filePath: glbPath, fromCache: true }
    }

    const { pathname, search } = new URL(raw, baseUrl || 'http://127.0.0.1:8000')
    const targetUrl = baseUrl ? `${baseUrl}${pathname}${search}` : raw

    const downloadKey = contentHash || crypto.createHash('sha1').update(pathname).digest('hex')
    const partialPath = getPartialPath(downloadKey)

    async function attemptDownload(resumeFromBytes = 0): Promise<null | string> {
      const headers: Record<string, string> = {}

      if (token) {
        headers['Authorization'] = `Bearer ${token}`
      }

      if (resumeFromBytes > 0) {
        headers['Range'] = `bytes=${resumeFromBytes}-`
      }

      const controller = new AbortController()
      let timer: NodeJS.Timeout | null = null

      function resetInactivityTimer() {
        if (timer) {
          clearTimeout(timer)
        }

        timer = setTimeout(() => {
          controller.abort(new Error(`Download stalled: no data received for ${inactivityTimeoutMs / 1000}s`))
        }, inactivityTimeoutMs)
      }

      resetInactivityTimer()

      let res: Response

      try {
        res = await fetchFn(targetUrl, {
          headers,
          signal: controller.signal
        })
      } catch (err) {
        if (timer) {
          clearTimeout(timer)
        }

        throw err
      }

      if (!res.ok && res.status !== 206) {
        if (timer) {
          clearTimeout(timer)
        }

        if (res.status === 416) {
          await fsp.unlink(partialPath).catch(() => {})
          const error: any = new Error('416 Range Not Satisfiable')
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

      const bodyStream = res.body as any
      let readableNodeStream: Readable

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
        if (timer) {
          clearTimeout(timer)
        }
      }

      const rawEtag = typeof res.headers?.get === 'function' ? res.headers.get('etag') : (res.headers as any)?.['etag']

      const rawSha =
        typeof res.headers?.get === 'function'
          ? res.headers.get('x-content-sha256')
          : (res.headers as any)?.['x-content-sha256']

      const headerSha = rawSha || rawEtag?.replace(/"/g, '')

      return headerSha || null
    }

    const stat = await fsp.stat(partialPath).catch(() => null)
    const resumeBytes = stat?.isFile() ? stat.size : 0

    let headerSha: null | string = null

    try {
      headerSha = await attemptDownload(resumeBytes)
    } catch (err: any) {
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
      contentHash: resolvedHash,
      filePath: finalGlbPath,
      fromCache: false
    }
  }

  async function ensureCached(
    opts: EnsureCachedOptions
  ): Promise<{ contentHash: string; filePath: string; fromCache: boolean }> {
    const raw = String(opts?.url || '')
    const key = `${opts?.contentHash || ''}:${raw}`

    if (inFlightDownloads.has(key)) {
      return await inFlightDownloads.get(key)!
    }

    const promise = _download(opts).finally(() => {
      inFlightDownloads.delete(key)
    })

    inFlightDownloads.set(key, promise)

    return await promise
  }

  return {
    ensureCached,
    getGlbPath,
    getPartialPath,
    getPath,
    has,
    readBuffer,
    sweep
  }
}
