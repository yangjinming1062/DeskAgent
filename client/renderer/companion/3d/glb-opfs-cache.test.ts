import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { blobKey, fetchGlbWithCache, GlbOpfsCache, metaKey } from './glb-opfs-cache'

class MockWritableStream {
  private buffer = new Uint8Array(0)
  private closed = false

  constructor(
    private readonly name: string,
    private readonly onCommit: (data: Uint8Array) => void
  ) {}

  async write(chunk: unknown): Promise<void> {
    if (this.closed) {
      throw new DOMException('Stream is closed', 'InvalidStateError')
    }

    if (typeof chunk === 'string') {
      this.buffer = new TextEncoder().encode(chunk)
    } else if (chunk instanceof ArrayBuffer) {
      this.buffer = new Uint8Array(chunk)
    } else if (chunk instanceof Uint8Array) {
      this.buffer = new Uint8Array(chunk)
    } else if (chunk && typeof chunk === 'object' && 'data' in chunk) {
      const data = (chunk as { data: unknown }).data

      if (typeof data === 'string') {
        this.buffer = new TextEncoder().encode(data)
      } else if (data instanceof ArrayBuffer) {
        this.buffer = new Uint8Array(data)
      } else if (data instanceof Uint8Array) {
        this.buffer = new Uint8Array(data)
      }
    }
  }

  async close(): Promise<void> {
    if (this.closed) {
      throw new DOMException('Stream is already closed', 'InvalidStateError')
    }

    this.closed = true
    this.onCommit(this.buffer)
  }
}

class MockFileHandle {
  readonly kind = 'file' as const

  constructor(
    public readonly name: string,
    private readonly getStorage: () => Map<string, Uint8Array>
  ) {}

  async getFile(): Promise<File> {
    const storage = this.getStorage()
    const data = storage.get(this.name)

    if (!data) {
      throw new DOMException(`File not found: ${this.name}`, 'NotFoundError')
    }

    return new File([data.buffer as ArrayBuffer], this.name)
  }

  async createWritable(): Promise<FileSystemWritableFileStream> {
    return new MockWritableStream(this.name, data => {
      this.getStorage().set(this.name, data)
    }) as unknown as FileSystemWritableFileStream
  }
}

class MockDirectoryHandle {
  readonly kind = 'directory' as const
  readonly name = 'glb-cache'
  private readonly files = new Map<string, Uint8Array>()
  public failFiles = new Set<string>()

  async getFileHandle(name: string, options?: { create?: boolean }): Promise<FileSystemFileHandle> {
    if (this.failFiles.has(name)) {
      throw new Error(`Simulated error for file: ${name}`)
    }

    if (!this.files.has(name)) {
      if (!options?.create) {
        throw new DOMException(`File not found: ${name}`, 'NotFoundError')
      }

      this.files.set(name, new Uint8Array(0))
    }

    return new MockFileHandle(name, () => this.files) as unknown as FileSystemFileHandle
  }

  async removeEntry(name: string): Promise<void> {
    if (!this.files.has(name)) {
      throw new DOMException(`File not found: ${name}`, 'NotFoundError')
    }

    this.files.delete(name)
  }

  async *values(): AsyncIterable<FileSystemHandle> {
    for (const name of Array.from(this.files.keys())) {
      yield new MockFileHandle(name, () => this.files) as unknown as FileSystemHandle
    }
  }

  hasFile(name: string): boolean {
    return this.files.has(name)
  }

  getFileData(name: string): Uint8Array | undefined {
    return this.files.get(name)
  }

  setFileData(name: string, data: Uint8Array): void {
    this.files.set(name, data)
  }

  asHandle(): FileSystemDirectoryHandle {
    return this as unknown as FileSystemDirectoryHandle
  }
}

function makeBuffer(size: number, fill = 42): ArrayBuffer {
  const u8 = new Uint8Array(size)
  u8.fill(fill)

  return u8.buffer
}

describe('glb-opfs-cache', () => {
  let mockDir: MockDirectoryHandle
  let cache: GlbOpfsCache

  beforeEach(() => {
    mockDir = new MockDirectoryHandle()
    cache = new GlbOpfsCache({
      getDirectory: async () => mockDir.asHandle()
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  describe('LRU 清理策略', () => {
    it('超过文件数上限时按 LRU 删除最久未使用的 entry', async () => {
      const smallCache = new GlbOpfsCache({
        getDirectory: async () => mockDir.asHandle(),
        maxFiles: 3,
        maxBytes: 10 * 1024 * 1024
      })

      await smallCache.write('hash1', makeBuffer(100, 1))
      await smallCache.write('hash2', makeBuffer(100, 2))
      await smallCache.write('hash3', makeBuffer(100, 3))

      expect(mockDir.hasFile(metaKey('hash1'))).toBe(true)
      expect(mockDir.hasFile(metaKey('hash2'))).toBe(true)
      expect(mockDir.hasFile(metaKey('hash3'))).toBe(true)

      // 把 hash1 的 writtenAt 推到未来，使其成为"最新"
      const meta1Text = new TextDecoder().decode(mockDir.getFileData(metaKey('hash1'))!)
      const meta1 = JSON.parse(meta1Text)
      meta1.writtenAt = Date.now() + 1000
      mockDir.setFileData(metaKey('hash1'), new TextEncoder().encode(JSON.stringify(meta1)))

      await smallCache.write('hash4', makeBuffer(100, 4))

      expect(mockDir.hasFile(metaKey('hash2'))).toBe(false)
      expect(mockDir.hasFile(blobKey('hash2'))).toBe(false)

      expect(mockDir.hasFile(metaKey('hash1'))).toBe(true)
      expect(mockDir.hasFile(blobKey('hash1'))).toBe(true)
      expect(mockDir.hasFile(metaKey('hash3'))).toBe(true)
      expect(mockDir.hasFile(blobKey('hash3'))).toBe(true)
      expect(mockDir.hasFile(metaKey('hash4'))).toBe(true)
      expect(mockDir.hasFile(blobKey('hash4'))).toBe(true)
    })

    it('超过总字节上限时按 LRU 顺序删除直到符合配额', async () => {
      const smallCache = new GlbOpfsCache({
        getDirectory: async () => mockDir.asHandle(),
        maxFiles: 10,
        maxBytes: 300
      })

      await smallCache.write('hashA', makeBuffer(100, 1))
      await smallCache.write('hashB', makeBuffer(100, 2))
      await smallCache.write('hashC', makeBuffer(100, 3))

      expect(mockDir.hasFile(metaKey('hashA'))).toBe(true)
      expect(mockDir.hasFile(metaKey('hashB'))).toBe(true)
      expect(mockDir.hasFile(metaKey('hashC'))).toBe(true)

      await smallCache.write('hashD', makeBuffer(150, 4))

      // 移除 A+B(200B) 后剩 C(100B)+D(150B)=250B 满足配额
      expect(mockDir.hasFile(metaKey('hashA'))).toBe(false)
      expect(mockDir.hasFile(blobKey('hashA'))).toBe(false)
      expect(mockDir.hasFile(metaKey('hashB'))).toBe(false)
      expect(mockDir.hasFile(blobKey('hashB'))).toBe(false)

      expect(mockDir.hasFile(metaKey('hashC'))).toBe(true)
      expect(mockDir.hasFile(blobKey('hashC'))).toBe(true)
      expect(mockDir.hasFile(metaKey('hashD'))).toBe(true)
      expect(mockDir.hasFile(blobKey('hashD'))).toBe(true)
    })
  })

  describe('并发安全性与队列串行化', () => {
    it('并发读取同一 hash 不会产生未处理 rejection 或数据破坏', async () => {
      const buffer = makeBuffer(512, 99)
      await cache.write('parallel_hash', buffer)

      const reads = Array.from({ length: 20 }, () => cache.read('parallel_hash'))
      const results = await Promise.all(reads)

      for (const res of results) {
        expect(res).not.toBeNull()
        expect(res!.byteLength).toBe(512)
        expect(new Uint8Array(res!)[0]).toBe(99)
      }

      expect(mockDir.hasFile(metaKey('parallel_hash'))).toBe(true)
      expect(mockDir.hasFile(blobKey('parallel_hash'))).toBe(true)
    })

    it('prune 期间写入的新 cache 不会被旧 snapshot 误删', async () => {
      const smallCache = new GlbOpfsCache({
        getDirectory: async () => mockDir.asHandle(),
        maxFiles: 2,
        maxBytes: 10 * 1024 * 1024
      })

      await smallCache.write('item1', makeBuffer(50, 1))
      await smallCache.write('item2', makeBuffer(50, 2))

      await Promise.all([smallCache.prune(), smallCache.write('item3', makeBuffer(50, 3))])

      expect(mockDir.hasFile(metaKey('item3'))).toBe(true)
      expect(mockDir.hasFile(blobKey('item3'))).toBe(true)
      expect(mockDir.hasFile(metaKey('item2'))).toBe(true)
      expect(mockDir.hasFile(blobKey('item2'))).toBe(true)
      expect(mockDir.hasFile(metaKey('item1'))).toBe(false)
      expect(mockDir.hasFile(blobKey('item1'))).toBe(false)
    })
  })

  describe('异常处理与半成品清理', () => {
    it('写入元数据失败时清理 blob 半成品，不留孤立文件', async () => {
      mockDir.failFiles.add(metaKey('fail_meta_hash'))

      await cache.write('fail_meta_hash', makeBuffer(200, 7))

      expect(mockDir.hasFile(blobKey('fail_meta_hash'))).toBe(false)
      expect(mockDir.hasFile(metaKey('fail_meta_hash'))).toBe(false)

      const readBack = await cache.read('fail_meta_hash')
      expect(readBack).toBeNull()
    })

    it('prune 自动清理无 meta 的孤立 blob 和损坏的 meta 文件', async () => {
      mockDir.setFileData(blobKey('orphan_blob'), new Uint8Array([1, 2, 3]))

      mockDir.setFileData(metaKey('corrupt_meta'), new TextEncoder().encode('not valid json {'))

      mockDir.setFileData(
        metaKey('wrong_ver'),
        new TextEncoder().encode(
          JSON.stringify({
            version: 999,
            contentHash: 'wrong_ver',
            size: 10,
            writtenAt: Date.now()
          })
        )
      )

      await cache.prune()

      expect(mockDir.hasFile(blobKey('orphan_blob'))).toBe(false)
      expect(mockDir.hasFile(metaKey('corrupt_meta'))).toBe(false)
      expect(mockDir.hasFile(metaKey('wrong_ver'))).toBe(false)
    })
  })

  describe('fetchGlbWithCache', () => {
    it('未命中时拉取并回填 OPFS 缓存，二次读取直接走缓存', async () => {
      const modelBuffer = makeBuffer(256, 123)

      const mockApiAssetBuffer = vi.fn().mockResolvedValue(new Uint8Array(modelBuffer))

      ;(window as unknown as { spiritagent?: unknown }).spiritagent = {
        apiAssetBuffer: mockApiAssetBuffer
      }

      const first = await fetchGlbWithCache('http://example.com/test.glb', 'hash_fetch_test', cache)
      expect(first).not.toBeNull()
      expect(mockApiAssetBuffer).toHaveBeenCalledTimes(1)

      // 等异步回写 flush（让串行队列处理完 fire-and-forget write）。
      await cache.write('__flush__', makeBuffer(1))

      mockApiAssetBuffer.mockClear()
      const second = await fetchGlbWithCache('http://example.com/test.glb', 'hash_fetch_test', cache)
      expect(second).not.toBeNull()
      expect(mockApiAssetBuffer).not.toHaveBeenCalled()
      expect(new Uint8Array(second!)[0]).toBe(123)

      delete (window as unknown as { spiritagent?: unknown }).spiritagent
    })
  })
})
