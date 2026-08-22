import { log } from '@/shared/lib/log'

// OPFS 是面向二进制 blob 的真实文件系统；Cache Storage 留给 HTTP 响应，不复用。

export const OPFS_DIR = 'glb-cache'
export const SCHEMA_VERSION = 1
export const MAX_CACHED_FILES = 5
export const MAX_CACHED_BYTES = 512 * 1024 * 1024
export const META_SUFFIX = '.meta.json'
export const BLOB_SUFFIX = '.glb'

export interface MetaFile {
  version: number
  contentHash: string
  writtenAt: number
  size: number
}

export function metaKey(contentHash: string): string {
  return `${contentHash}${META_SUFFIX}`
}

export function blobKey(contentHash: string): string {
  return `${contentHash}${BLOB_SUFFIX}`
}

export function isMetaFile(name: string): boolean {
  return name.endsWith(META_SUFFIX)
}

export function hashFromMetaFile(name: string): string | null {
  if (!name.endsWith(META_SUFFIX)) {
    return null
  }

  return name.slice(0, -META_SUFFIX.length)
}

export function isBlobFile(name: string): boolean {
  return name.endsWith(BLOB_SUFFIX)
}

export function hashFromBlobFile(name: string): string | null {
  if (!name.endsWith(BLOB_SUFFIX)) {
    return null
  }

  return name.slice(0, -BLOB_SUFFIX.length)
}

function defaultRootDir(): Promise<FileSystemDirectoryHandle | null> {
  if (typeof navigator === 'undefined' || !navigator.storage?.getDirectory) {
    return Promise.resolve(null)
  }

  return navigator.storage
    .getDirectory()
    .then(root => root.getDirectoryHandle(OPFS_DIR, { create: true }))
    .catch(err => {
      log.warn('glb-opfs-cache', 'OPFS unavailable:', err)

      return null
    })
}

export interface GlbOpfsCacheOptions {
  getDirectory?: () => Promise<FileSystemDirectoryHandle | null>
  maxFiles?: number
  maxBytes?: number
}

export class GlbOpfsCache {
  private readonly getDir: () => Promise<FileSystemDirectoryHandle | null>
  private readonly maxFiles: number
  private readonly maxBytes: number
  private queue: Promise<unknown> = Promise.resolve()
  private readonly lastTouched = new Map<string, number>()

  constructor(options?: GlbOpfsCacheOptions) {
    this.getDir = options?.getDirectory ?? defaultRootDir
    this.maxFiles = options?.maxFiles ?? MAX_CACHED_FILES
    this.maxBytes = options?.maxBytes ?? MAX_CACHED_BYTES
  }

  private runSerialized<T>(task: () => Promise<T>): Promise<T> {
    const next = this.queue.then(
      () => task(),
      () => task()
    )

    this.queue = next.then(
      () => undefined,
      () => undefined
    )

    return next
  }

  /** 未命中或 OPFS 错误时返回 null，调用方据此回退到 IPC。 */
  async read(contentHash: string): Promise<ArrayBuffer | null> {
    if (!contentHash) {
      return null
    }

    const dir = await this.getDir()

    if (!dir) {
      return null
    }

    try {
      const metaHandle = await dir.getFileHandle(metaKey(contentHash))
      const metaFile = await metaHandle.getFile()
      const meta = JSON.parse(await metaFile.text()) as Partial<MetaFile>

      if (
        meta.version !== SCHEMA_VERSION ||
        meta.contentHash !== contentHash ||
        typeof meta.size !== 'number' ||
        meta.size < 0 ||
        typeof meta.writtenAt !== 'number'
      ) {
        return null
      }

      const blobHandle = await dir.getFileHandle(blobKey(contentHash))
      const blobFile = await blobHandle.getFile()

      if (blobFile.size !== meta.size) {
        return null
      }

      const buffer = await blobFile.arrayBuffer()

      // 命中时回写 writtenAt 维持 LRU 顺序；通过串行队列与后续 write/prune 排队。
      void this.runSerialized(() => this.touchInternal(dir, contentHash))

      return buffer
    } catch {
      return null
    }
  }

  /** 写入失败（配额等）时静默吞掉，调用方流水线继续运行。 */
  async write(contentHash: string, bytes: ArrayBuffer): Promise<void> {
    if (!contentHash || !bytes) {
      return
    }

    await this.runSerialized(() => this.writeInternal(contentHash, bytes))
  }

  async prune(maxFiles = this.maxFiles, maxBytes = this.maxBytes): Promise<void> {
    const dir = await this.getDir()

    if (!dir) {
      return
    }

    await this.runSerialized(() => this.pruneInternal(dir, maxFiles, maxBytes))
  }

  private async touchInternal(dir: FileSystemDirectoryHandle, contentHash: string): Promise<void> {
    const now = Date.now()
    const last = this.lastTouched.get(contentHash) ?? 0

    if (now - last < 1000) {
      return
    }

    try {
      const metaHandle = await dir.getFileHandle(metaKey(contentHash))
      const metaFile = await metaHandle.getFile()
      const meta = JSON.parse(await metaFile.text()) as Partial<MetaFile>

      if (meta.version !== SCHEMA_VERSION || meta.contentHash !== contentHash) {
        return
      }

      // 先确认 blob 仍在，避免给已被 prune 掉的 blob 续 meta。
      await dir.getFileHandle(blobKey(contentHash))

      meta.writtenAt = now
      const writable = await metaHandle.createWritable()
      await writable.write(JSON.stringify(meta))
      await writable.close()
      this.lastTouched.set(contentHash, now)
    } catch {}
  }

  private async writeInternal(contentHash: string, bytes: ArrayBuffer): Promise<void> {
    const dir = await this.getDir()

    if (!dir) {
      return
    }

    let blobWritten = false
    let metaWritten = false

    try {
      const blobHandle = await dir.getFileHandle(blobKey(contentHash), { create: true })
      const blobWritable = await blobHandle.createWritable()
      await blobWritable.write(bytes)
      await blobWritable.close()
      blobWritten = true

      const meta: MetaFile = {
        contentHash,
        size: bytes.byteLength,
        version: SCHEMA_VERSION,
        writtenAt: Date.now()
      }

      const metaHandle = await dir.getFileHandle(metaKey(contentHash), { create: true })
      const metaWritable = await metaHandle.createWritable()
      await metaWritable.write(JSON.stringify(meta))
      await metaWritable.close()
      metaWritten = true
      this.lastTouched.set(contentHash, meta.writtenAt)

      await this.pruneInternal(dir, this.maxFiles, this.maxBytes)
    } catch (err) {
      log.warn('glb-opfs-cache', 'write failed:', err)

      if (blobWritten || metaWritten) {
        try {
          await dir.removeEntry(blobKey(contentHash))
        } catch {}

        try {
          await dir.removeEntry(metaKey(contentHash))
        } catch {}

        this.lastTouched.delete(contentHash)
      }
    }
  }

  private async pruneInternal(dir: FileSystemDirectoryHandle, maxFiles: number, maxBytes: number): Promise<void> {
    try {
      const metaEntries: { hash: string; size: number; writtenAt: number }[] = []
      const blobHashes = new Set<string>()
      const metaHashes = new Set<string>()

      for await (const handle of (dir as unknown as { values: () => AsyncIterable<FileSystemHandle> }).values()) {
        if (handle.kind === 'file' && typeof handle.name === 'string') {
          if (isMetaFile(handle.name)) {
            const hash = hashFromMetaFile(handle.name)

            if (!hash) {
              continue
            }

            metaHashes.add(hash)

            try {
              const file = await (handle as FileSystemFileHandle).getFile()
              const meta = JSON.parse(await file.text()) as Partial<MetaFile>

              if (
                meta.version === SCHEMA_VERSION &&
                meta.contentHash === hash &&
                typeof meta.writtenAt === 'number' &&
                typeof meta.size === 'number' &&
                meta.size >= 0
              ) {
                metaEntries.push({
                  hash: meta.contentHash,
                  size: meta.size,
                  writtenAt: meta.writtenAt
                })
              } else {
                try {
                  await dir.removeEntry(handle.name)
                } catch {}
              }
            } catch {
              try {
                await dir.removeEntry(handle.name)
              } catch {}
            }
          } else if (isBlobFile(handle.name)) {
            const hash = hashFromBlobFile(handle.name)

            if (hash) {
              blobHashes.add(hash)
            }
          }
        }
      }

      for (const blobHash of blobHashes) {
        if (!metaHashes.has(blobHash)) {
          try {
            await dir.removeEntry(blobKey(blobHash))
          } catch {}
        }
      }

      const validEntries: typeof metaEntries = []
      let totalBytes = 0

      for (const entry of metaEntries) {
        if (blobHashes.has(entry.hash)) {
          validEntries.push(entry)
          totalBytes += entry.size
        } else {
          try {
            await dir.removeEntry(metaKey(entry.hash))
          } catch {}

          this.lastTouched.delete(entry.hash)
        }
      }

      if (validEntries.length <= maxFiles && totalBytes <= maxBytes) {
        return
      }

      validEntries.sort((a, b) => a.writtenAt - b.writtenAt)

      while (validEntries.length > maxFiles || totalBytes > maxBytes) {
        const oldest = validEntries.shift()

        if (!oldest) {
          break
        }

        try {
          await dir.removeEntry(blobKey(oldest.hash))
        } catch {}

        try {
          await dir.removeEntry(metaKey(oldest.hash))
        } catch {}

        this.lastTouched.delete(oldest.hash)
        totalBytes -= oldest.size
      }
    } catch (err) {
      log.warn('glb-opfs-cache', 'prune failed:', err)
    }
  }
}

export const defaultGlbOpfsCache = new GlbOpfsCache()

// 键是 contentHash 而非 URL —— 后端的签名 URL 查询串会轮换。
export async function fetchGlbWithCache(
  url: string,
  contentHash?: string,
  cacheInstance: GlbOpfsCache = defaultGlbOpfsCache
): Promise<ArrayBuffer | null> {
  if (contentHash) {
    const cached = await cacheInstance.read(contentHash)

    if (cached) {
      return cached
    }
  }

  let bytes: ArrayBuffer | null = null

  try {
    if (typeof window.spiritagent?.apiAssetModelUrl === 'function') {
      const mediaUrl = await window.spiritagent.apiAssetModelUrl({
        url,
        contentHash: contentHash || undefined
      })

      const res = await fetch(mediaUrl)

      if (!res.ok) {
        throw new Error(`Media protocol fetch failed with status ${res.status}`)
      }

      bytes = await res.arrayBuffer()
    } else {
      const u8 = await window.spiritagent.apiAssetBuffer({
        url,
        contentHash: contentHash || undefined
      })

      bytes = u8.slice().buffer
    }
  } catch (err) {
    log.warn('glb-opfs-cache', 'GLB fetch failed:', err)

    return null
  }

  if (contentHash && bytes) {
    // 调用方已拿到数据，缓存写入不阻塞主流程。
    void cacheInstance.write(contentHash, bytes)
  }

  return bytes
}
