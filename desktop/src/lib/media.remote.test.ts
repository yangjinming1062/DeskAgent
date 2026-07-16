import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $connection } from '@/store/session'

import { filePathFromMediaPath, gatewayMediaDataUrl, isRemoteGateway } from './media'

describe('isRemoteGateway', () => {
  afterEach(() => {
    $connection.set(null)
  })

  it('is false with no connection', () => {
    $connection.set(null)
    expect(isRemoteGateway()).toBe(false)
  })

  it('is false in local mode', () => {
    $connection.set({ mode: 'local' } as never)
    expect(isRemoteGateway()).toBe(false)
  })

  it('is true in remote mode', () => {
    $connection.set({ mode: 'remote' } as never)
    expect(isRemoteGateway()).toBe(true)
  })
})

describe('filePathFromMediaPath', () => {
  it('passes through a plain path', () => {
    expect(filePathFromMediaPath('/home/u/.zast/images/a.png')).toBe('/home/u/.zast/images/a.png')
  })

  it('decodes a file:// URL with encoded characters', () => {
    expect(filePathFromMediaPath('file:///tmp/a%20b.png')).toBe('/tmp/a b.png')
  })
})

describe('gatewayMediaDataUrl', () => {
  const readFileDataUrl = vi.fn(async () => 'data:image/png;base64,ZHVtbXk=')

  beforeEach(() => {
    readFileDataUrl.mockClear()
    vi.stubGlobal('window', { zastDesktop: { readFileDataUrl } })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('reads the local file via readFileDataUrl', async () => {
    const url = await gatewayMediaDataUrl('file:///home/u/.zast/images/a%20b.png')

    expect(url).toBe('data:image/png;base64,ZHVtbXk=')
    expect(readFileDataUrl).toHaveBeenCalledWith('/home/u/.zast/images/a b.png')
  })

  it('errors when readFileDataUrl is not exposed', async () => {
    vi.stubGlobal('window', { zastDesktop: {} })
    await expect(gatewayMediaDataUrl('/home/u/.zast/images/a.png')).rejects.toThrow(/readFileDataUrl/)
  })
})
