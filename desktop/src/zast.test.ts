import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { listSessions } from './zast'

const emptySessionsResponse = {
  limit: 0,
  offset: 0,
  sessions: [],
  total: 0
}

describe('Zast REST session helpers', () => {
  let api: ReturnType<typeof vi.fn>

  beforeEach(() => {
    api = vi.fn().mockResolvedValue(emptySessionsResponse)
    Object.defineProperty(window, 'zastDesktop', {
      configurable: true,
      value: { api }
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    Reflect.deleteProperty(window, 'zastDesktop')
  })

  it('uses a longer timeout for the single-profile session list', async () => {
    await listSessions(50, 1)

    expect(api).toHaveBeenCalledWith(
      expect.objectContaining({
        path: '/api/sessions?limit=50&offset=0&min_messages=1&archived=exclude&order=recent',
        timeoutMs: 60_000
      })
    )
  })

  it('omits include_subagents by default to keep sidebar hiding subagents', async () => {
    await listSessions(50, 1)

    expect(api.mock.calls[0]?.[0].path).not.toContain('include_subagents')
  })

  it('appends include_subagents=true when opted in', async () => {
    await listSessions(50, 1, 'exclude', 'recent', true)

    expect(api).toHaveBeenCalledWith(
      expect.objectContaining({
        path: '/api/sessions?limit=50&offset=0&min_messages=1&archived=exclude&order=recent&include_subagents=true'
      })
    )
  })
})
