import { log } from '@/shared/lib/log'

/** OPFS-backed cache for raw GLB bytes — second load of the same model is served from disk instead of full IPC + signed URL round trip (typical 200–800 ms → 0 ms). Uses OPFS Web standard, NOT `caches.open` (Cache Storage is for HTTP responses; OPFS is a real filesystem for binary blobs). Silently falls back to the IPC path on any OPFS failure. */

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

/** Read cached GLB bytes for `contentHash`. Returns null on miss or any OPFS error — caller treats null as "fall back to IPC". */
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

    return await blobFile.arrayBuffer()
  } catch {
    return null
  }
}

/** Persist GLB bytes in OPFS. Best-effort: if the write fails (quota, etc.) we swallow the error so the calling pipeline can keep working. */
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
      version: SCHEMA_VERSION,
      contentHash,
      writtenAt: Date.now(),
      size: bytes.byteLength
    }

    const metaHandle = await dir.getFileHandle(metaKey(contentHash), { create: true })
    const metaWritable = await metaHandle.createWritable()
    await metaWritable.write(JSON.stringify(meta))
    await metaWritable.close()
  } catch (err) {
    log.warn('glb-opfs-cache', 'write failed:', err)
  }
}

/** Cache-aware wrapper around the main-process `apiAssetBuffer` IPC. Tries OPFS first, falls back to network IPC and populates the cache on success. Key is `contentHash` (server-issued), not URL — the URL is a signed query that rotates. */
export async function fetchGlbWithCache(url: string, contentHash?: string): Promise<ArrayBuffer | null> {
  if (contentHash) {
    const cached = await readCachedGlb(contentHash)

    if (cached) {
      return cached
    }
  }

  let bytes: ArrayBuffer | null = null

  try {
    const u8 = await window.spiritagent.apiAssetBuffer({
      url,
      contentHash: contentHash || undefined
    })

    bytes = u8.slice().buffer
  } catch (err) {
    log.warn('glb-opfs-cache', 'IPC fetch failed:', err)

    return null
  }

  if (contentHash && bytes) {
    // Fire-and-forget — caller already has the data and shouldn't wait for the cache write to resolve.
    void writeCachedGlb(contentHash, bytes)
  }

  return bytes
}
