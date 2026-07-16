import { describe, expect, it, vi } from 'vitest'

import { resolveGatewayWsUrl } from './gateway-ws-url'

const conn = { wsUrl: 'ws://host/api/ws?token=abc' }

describe('resolveGatewayWsUrl', () => {
  it('uses the minted URL when available', async () => {
    const getGatewayWsUrl = vi.fn().mockResolvedValue('ws://host/api/ws?token=fresh')
    await expect(resolveGatewayWsUrl({ getGatewayWsUrl }, conn)).resolves.toBe('ws://host/api/ws?token=fresh')
  })

  it('falls back to the cached URL when minting fails', async () => {
    const getGatewayWsUrl = vi.fn().mockRejectedValue(new Error('transient'))
    await expect(resolveGatewayWsUrl({ getGatewayWsUrl }, conn)).resolves.toBe(conn.wsUrl)
  })

  it('falls back to the cached URL when the preload method is absent', async () => {
    await expect(resolveGatewayWsUrl({}, conn)).resolves.toBe(conn.wsUrl)
  })
})
