import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MemoryBrowser } from './memory-browser'
import { setMemoryBrowserTab } from './memory-browser-store'

const requestGateway = vi.fn()

vi.mock('@/companion/boot/use-gateway-request', () => ({
  useGatewayRequest: () => ({ requestGateway })
}))

vi.mock('@/companion/interactive-regions', () => ({
  useInteractiveRegion: () => undefined
}))

beforeEach(() => {
  requestGateway.mockReset()
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  setMemoryBrowserTab('recall')
})

describe('MemoryBrowser', () => {
  it('renders loading then list on mount', async () => {
    requestGateway.mockResolvedValueOnce({
      memories: [
        {
          id: 1,
          context: 'recall:a',
          tags: '["likes"]',
          content: 'post-rock',
          created_at: null,
          updated_at: '2026-08-05T00:00:00Z'
        }
      ],
      counts: { recall: 1, auto_inject: 0, user_profile: 0, interaction_stats: 0, other: 0 }
    })

    const { getByText, getByDisplayValue } = render(<MemoryBrowser onClose={vi.fn()} />)
    await waitFor(() => getByDisplayValue('post-rock'))
    expect(getByText(content => content.includes('recall:a'))).toBeTruthy()
  })

  it('optimistic edit rolls back on RPC failure', async () => {
    requestGateway.mockResolvedValueOnce({
      memories: [
        {
          id: 1,
          context: 'recall:a',
          tags: '["likes"]',
          content: 'old',
          created_at: null,
          updated_at: '2026-08-05T00:00:00Z'
        }
      ],
      counts: { recall: 1, auto_inject: 0, user_profile: 0, interaction_stats: 0, other: 0 }
    })

    const { getByDisplayValue, getByText } = render(<MemoryBrowser onClose={vi.fn()} />)
    await waitFor(() => getByDisplayValue('old'))

    requestGateway.mockRejectedValueOnce(new Error('boom'))

    const textarea = getByDisplayValue('old')
    act(() => {
      fireEvent.change(textarea, { target: { value: 'new' } })
    })

    await waitFor(() => getByText('保存'))
    await act(async () => {
      fireEvent.click(getByText('保存'))
    })
    await waitFor(() => getByDisplayValue('old'))
    expect(getByText('保存失败，已回滚')).toBeTruthy()
  })

  it('optimistic delete rolls back on RPC failure', async () => {
    requestGateway.mockResolvedValueOnce({
      memories: [
        {
          id: 1,
          context: 'recall:a',
          tags: '["likes"]',
          content: 'a',
          created_at: null,
          updated_at: '2026-08-05T00:00:00Z'
        },
        {
          id: 2,
          context: 'recall:b',
          tags: '["likes"]',
          content: 'b',
          created_at: null,
          updated_at: '2026-08-05T00:00:00Z'
        }
      ],
      counts: { recall: 2, auto_inject: 0, user_profile: 0, interaction_stats: 0, other: 0 }
    })

    const { getAllByText } = render(<MemoryBrowser onClose={vi.fn()} />)
    await waitFor(() => getAllByText('删除'))

    requestGateway.mockRejectedValueOnce(new Error('boom'))
    await act(async () => {
      fireEvent.click(getAllByText('删除')[0])
    })
    await waitFor(() => getAllByText('删除'))
    expect(getAllByText('删除失败，已回滚').length).toBeGreaterThanOrEqual(1)
  })

  it('tab switch reloads with new kind', async () => {
    requestGateway.mockResolvedValueOnce({
      memories: [{ id: 1, context: 'recall:a', tags: '["likes"]', content: 'a', created_at: null, updated_at: null }],
      counts: { recall: 1, auto_inject: 0, user_profile: 0, interaction_stats: 0, other: 0 }
    })
    requestGateway.mockResolvedValueOnce({
      memories: [],
      counts: { recall: 1, auto_inject: 0, user_profile: 0, interaction_stats: 0, other: 0 }
    })

    const { getByText, queryByText } = render(<MemoryBrowser onClose={vi.fn()} />)
    await waitFor(() => getByText('a'))
    await act(async () => {
      fireEvent.click(getByText(/自动注入/))
    })
    await waitFor(() => expect(requestGateway).toHaveBeenCalledWith('memory.list', { kind: 'auto_inject' }))
    expect(queryByText('a')).toBeNull()
  })
})
