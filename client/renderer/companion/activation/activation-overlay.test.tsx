import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ActivationOverlay } from '@/companion/activation/activation-overlay'
import { $auth } from '@/shared/store/auth'
import type * as authStore from '@/shared/store/auth'

const activateMock = vi.fn()
const useInteractiveRegionMock = vi.fn()

vi.mock('@/companion/interactive-regions', () => ({
  useInteractiveRegion: (...args: unknown[]) => useInteractiveRegionMock(...args)
}))

vi.mock('@/shared/store/auth', async importOriginal => {
  const actual = await importOriginal<typeof authStore>()

  return {
    ...actual,
    activate: (...args: unknown[]) => activateMock(...args)
  }
})

describe('ActivationOverlay', () => {
  beforeEach(() => {
    activateMock.mockReset()
    useInteractiveRegionMock.mockReset()
    $auth.set({ kind: 'unauthenticated' })
  })

  afterEach(() => {
    cleanup()
  })

  it('renders activation title, textarea, cancel, and submit buttons', () => {
    const onClose = vi.fn()
    render(<ActivationOverlay onClose={onClose} />)

    expect(screen.getByText('激活 SpiritAgent')).toBeDefined()
    expect(screen.getByPlaceholderText('在此粘贴激活码…')).toBeDefined()
    expect(screen.getByRole('button', { name: '取消' })).toBeDefined()
    expect(screen.getByRole('button', { name: '激活' })).toBeDefined()
    expect(screen.getByRole('button', { name: '关闭' })).toBeDefined()
  })

  it('calls onClose when clicking the cancel button', () => {
    const onClose = vi.fn()
    render(<ActivationOverlay onClose={onClose} />)

    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('calls onClose when clicking the close button (X)', () => {
    const onClose = vi.fn()
    render(<ActivationOverlay onClose={onClose} />)

    fireEvent.click(screen.getByRole('button', { name: '关闭' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('calls onClose when pressing Escape key', () => {
    const onClose = vi.fn()
    render(<ActivationOverlay onClose={onClose} />)

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('calls onClose when clicking the backdrop overlay', () => {
    const onClose = vi.fn()
    const { container } = render(<ActivationOverlay onClose={onClose} />)

    const backdrop = container.firstElementChild as HTMLElement
    fireEvent.click(backdrop)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('does not call onClose when clicking inside the form container', () => {
    const onClose = vi.fn()
    render(<ActivationOverlay onClose={onClose} />)

    const form = screen.getByRole('textbox')
    fireEvent.click(form)
    expect(onClose).not.toHaveBeenCalled()
  })

  it('displays auth error message when unauthenticated with error', () => {
    $auth.set({ kind: 'unauthenticated', error: '激活码无效或已过期' })
    const onClose = vi.fn()
    render(<ActivationOverlay onClose={onClose} />)

    expect(screen.getByText('激活码无效或已过期')).toBeDefined()
  })

  it('submits activation code and closes on success', async () => {
    activateMock.mockResolvedValueOnce({
      hasToken: true,
      tokenExpiresAt: Date.now() + 3600000,
      user: { id: 1, name: 'Tester' }
    })

    const onClose = vi.fn()
    render(<ActivationOverlay onClose={onClose} />)

    const textarea = screen.getByPlaceholderText('在此粘贴激活码…')
    fireEvent.change(textarea, { target: { value: 'TEST_ACTIVATION_CODE_123' } })

    const submitBtn = screen.getByRole('button', { name: '激活' })
    fireEvent.click(submitBtn)

    expect(activateMock).toHaveBeenCalledWith({ code: 'TEST_ACTIVATION_CODE_123' })
    await waitFor(() => {
      expect(onClose).toHaveBeenCalledTimes(1)
    })
  })
})
