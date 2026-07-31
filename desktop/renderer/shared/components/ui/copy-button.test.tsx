import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { CopyButton } from './copy-button'

describe('CopyButton', () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('uses default labels and copied feedback', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText }
    })

    render(<CopyButton text="hello" />)

    const button = screen.getByRole('button', { name: '复制' })

    expect(button.textContent).toContain('复制')
    fireEvent.click(button)

    await waitFor(() => expect(writeText).toHaveBeenCalledWith('hello'))
    await waitFor(() => expect(screen.getByRole('button', { name: '已复制' })).toBeTruthy())
    expect(screen.getByRole('button', { name: '已复制' }).textContent).toContain('已复制')
  })
})
