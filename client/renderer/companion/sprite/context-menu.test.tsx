import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SpriteContextMenu } from '@/companion/sprite/context-menu'
import { $contextMenuPos } from '@/companion/sprite/context-menu-store'
import { $auth } from '@/shared/store/auth'

const useInteractiveRegionMock = vi.fn()
const isRegionHitMock = vi.fn()

vi.mock('@/companion/interactive-regions', () => ({
  isRegionHit: (...args: unknown[]) => isRegionHitMock(...args),
  useInteractiveRegion: (...args: unknown[]) => useInteractiveRegionMock(...args)
}))

describe('SpriteContextMenu', () => {
  beforeEach(() => {
    useInteractiveRegionMock.mockReset()
    isRegionHitMock.mockReset()
    $contextMenuPos.set(null)
    $auth.set({
      kind: 'authenticated',
      snapshot: {
        baseUrl: 'http://127.0.0.1:8000',
        hasToken: true,
        tokenExpiresAt: Date.now() + 3600000,
        user: { id: 1, username: 'tester' }
      }
    })
  })

  afterEach(() => {
    cleanup()
  })

  it('renders hidden and inert when context menu position is null', () => {
    const { container } = render(
      <SpriteContextMenu
        onOpenChat={vi.fn()}
        onOpenMemory={vi.fn()}
        onOpenSettings={vi.fn()}
        onOpenVoiceCall={vi.fn()}
      />
    )

    const backdrop = container.firstElementChild as HTMLElement
    expect(backdrop.style.visibility).toBe('hidden')
    expect(backdrop.style.pointerEvents).toBe('none')
  })

  it('renders visible when position is set and triggers actions on button clicks', () => {
    $contextMenuPos.set({ x: 100, y: 150 })
    const onOpenChat = vi.fn()

    render(
      <SpriteContextMenu
        onOpenChat={onOpenChat}
        onOpenMemory={vi.fn()}
        onOpenSettings={vi.fn()}
        onOpenVoiceCall={vi.fn()}
      />
    )

    const chatBtn = screen.getByRole('button', { name: /对话 \(Talk\)/i })
    expect(chatBtn).toBeDefined()

    fireEvent.click(chatBtn)
    expect(onOpenChat).toHaveBeenCalledTimes(1)
    expect($contextMenuPos.get()).toBeNull()
  })

  it('closes menu when clicking outside on the backdrop', () => {
    $contextMenuPos.set({ x: 200, y: 300 })

    const { container } = render(
      <SpriteContextMenu
        onOpenChat={vi.fn()}
        onOpenMemory={vi.fn()}
        onOpenSettings={vi.fn()}
        onOpenVoiceCall={vi.fn()}
      />
    )

    const backdrop = container.firstElementChild as HTMLElement
    expect(backdrop.style.visibility).toBe('visible')

    fireEvent.pointerDown(backdrop, { clientX: 10, clientY: 10 })
    expect($contextMenuPos.get()).toBeNull()
  })

  it('closes menu on Escape key down', () => {
    $contextMenuPos.set({ x: 200, y: 300 })

    render(
      <SpriteContextMenu
        onOpenChat={vi.fn()}
        onOpenMemory={vi.fn()}
        onOpenSettings={vi.fn()}
        onOpenVoiceCall={vi.fn()}
      />
    )

    fireEvent.keyDown(window, { key: 'Escape' })
    expect($contextMenuPos.get()).toBeNull()
  })

  it('closes menu on window blur', () => {
    $contextMenuPos.set({ x: 200, y: 300 })

    render(
      <SpriteContextMenu
        onOpenChat={vi.fn()}
        onOpenMemory={vi.fn()}
        onOpenSettings={vi.fn()}
        onOpenVoiceCall={vi.fn()}
      />
    )

    fireEvent(window, new Event('blur'))
    expect($contextMenuPos.get()).toBeNull()
  })

  it('repositions menu when right-clicking on sprite stage while menu is open', () => {
    $contextMenuPos.set({ x: 100, y: 100 })
    isRegionHitMock.mockImplementation((id: string) => id === 'sprite-stage')

    const { container } = render(
      <SpriteContextMenu
        onOpenChat={vi.fn()}
        onOpenMemory={vi.fn()}
        onOpenSettings={vi.fn()}
        onOpenVoiceCall={vi.fn()}
      />
    )

    const backdrop = container.firstElementChild as HTMLElement
    fireEvent.contextMenu(backdrop, { clientX: 250, clientY: 350 })

    expect($contextMenuPos.get()).toEqual({ x: 250, y: 350 })
  })

  it('closes menu when right-clicking outside the sprite stage', () => {
    $contextMenuPos.set({ x: 100, y: 100 })
    isRegionHitMock.mockReturnValue(false)

    const { container } = render(
      <SpriteContextMenu
        onOpenChat={vi.fn()}
        onOpenMemory={vi.fn()}
        onOpenSettings={vi.fn()}
        onOpenVoiceCall={vi.fn()}
      />
    )

    const backdrop = container.firstElementChild as HTMLElement
    fireEvent.contextMenu(backdrop, { clientX: 500, clientY: 500 })

    expect($contextMenuPos.get()).toBeNull()
  })
})
