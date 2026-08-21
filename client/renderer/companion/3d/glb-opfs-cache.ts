import { log } from '@/shared/lib/log'

/** 基于 OPFS 的原始 GLB 字节缓存 —— 同一模型的二次加载直接从磁盘取，省去 IPC + 签名 URL 往返（典型 200–800 ms → 0 ms）。用 OPFS Web 标准，而不是 `caches.open`（Cache Storage 是给 HTTP 响应用的；OPFS 才是面向二进制 blob 的真实文件系统）。任何 OPFS 失败都静默回退到 IPC 路径。 */

const OPFS_DIR = 'glb-cache'
const SCHEMA_VERSION = 1

interface MetaFile {
  version: number
  contentHash: string
  writtenAt: number
  size: number
}

function rootDir(): Promise<FileSystemDirectoryHandle | null> {
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

function metaKey(contentHash: string): string {
  return `${contentHash}.meta`
}

function blobKey(contentHash: string): string {
  return `${contentHash}.glb`
}

/** 读取 `contentHash` 对应的缓存 GLB 字节。未命中或任何 OPFS 错误时返回 null —— 调用方把 null 当作"回退到 IPC"。 */
export async function readCachedGlb(contentHash: string): Promise<ArrayBuffer | null> {
  if (!contentHash) {
    return null
  }

  const dir = await rootDir()

  if (!dir) {
    return null
  }

  try {
    const metaHandle = await dir.getFileHandle(metaKey(contentHash))
    const metaFile = await metaHandle.getFile()
    const meta = JSON.parse(await metaFile.text()) as MetaFile

    if (meta.version !== SCHEMA_VERSION || meta.contentHash !== contentHash) {
      return null
    }

    const blobHandle = await dir.getFileHandle(blobKey(contentHash))
    const blobFile = await blobHandle.getFile()

    if (blobFile.size !== meta.size) {
      return null
    }
    // 命中时异步回写 writtenAt，维持 LRU 顺序
    void (async () => {
      try {
        meta.writtenAt = Date.now()
        const updatedMetaHandle = await dir.getFileHandle(metaKey(contentHash), { create: true })
        const writable = await updatedMetaHandle.createWritable()
        await writable.write(JSON.stringify(meta))
        await writable.close()
      } catch {
        /* 忽略更新时间戳失败 */
      }
    })()

    return await blobFile.arrayBuffer()
  } catch {
    return null
  }
}

const MAX_CACHED_FILES = 5
const MAX_CACHED_BYTES = 512 * 1024 * 1024

async function pruneGlbCache(
  dir: FileSystemDirectoryHandle,
  maxFiles = MAX_CACHED_FILES,
  maxBytes = MAX_CACHED_BYTES
): Promise<void> {
  try {
    const entries: { hash: string; size: number; writtenAt: number }[] = []
    let totalBytes = 0

    for await (const handle of (dir as unknown as { values: () => AsyncIterable<FileSystemHandle> }).values()) {
      if (handle.kind === 'file' && typeof handle.name === 'string' && handle.name.endsWith('.meta.json')) {
        try {
          const file = await (handle as FileSystemFileHandle).getFile()
          const meta = JSON.parse(await file.text()) as MetaFile

          if (meta.contentHash && typeof meta.writtenAt === 'number') {
            entries.push({
              hash: meta.contentHash,
              size: typeof meta.size === 'number' ? meta.size : 0,
              writtenAt: meta.writtenAt
            })
            totalBytes += meta.size || 0
          }
        } catch {
          /* 忽略异常 meta */
        }
      }
    }

    if (entries.length <= maxFiles && totalBytes <= maxBytes) {
      return
    }

    // 最久未访问的排前面
    entries.sort((a, b) => a.writtenAt - b.writtenAt)

    while (entries.length > maxFiles || totalBytes > maxBytes) {
      const oldest = entries.shift()

      if (!oldest) {
        break
      }

      try {
        await dir.removeEntry(blobKey(oldest.hash))
        await dir.removeEntry(metaKey(oldest.hash))
        totalBytes -= oldest.size
      } catch {
        /* 忽略删除异常 */
      }
    }
  } catch (err) {
    log.warn('glb-opfs-cache', 'prune failed:', err)
  }
}

/** 把 GLB 字节写入 OPFS。尽力而为：写入失败（配额等）时静默吞掉错误，让调用方的流水线继续运行。 */
export async function writeCachedGlb(contentHash: string, bytes: ArrayBuffer): Promise<void> {
  if (!contentHash) {
    return
  }

  const dir = await rootDir()

  if (!dir) {
    return
  }

  try {
    const blobHandle = await dir.getFileHandle(blobKey(contentHash), { create: true })
    const blobWritable = await blobHandle.createWritable()
    await blobWritable.write(bytes)
    await blobWritable.close()

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

    void pruneGlbCache(dir)
  } catch (err) {
    log.warn('glb-opfs-cache', 'write failed:', err)
  }
}

/** 围绕主进程模型缓存与流媒体协议的带缓存包装。先尝试 OPFS，失败则走自定义协议流式 fetch，成功后回填 OPFS 缓存。键是 `contentHash`（后端签发），不是 URL —— URL 是会轮换的签名查询串。 */
export async function fetchGlbWithCache(url: string, contentHash?: string): Promise<ArrayBuffer | null> {
  if (contentHash) {
    const cached = await readCachedGlb(contentHash)

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
    // 即发即忘 —— 调用方已经拿到数据，不应该等缓存写入完成。
    void writeCachedGlb(contentHash, bytes)
  }

  return bytes
}
