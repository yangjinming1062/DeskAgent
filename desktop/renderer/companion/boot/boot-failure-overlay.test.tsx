import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $desktopBoot, type DesktopBootState } from '@/companion/boot-store'
import { BootFailureOverlay } from '@/companion/boot/boot-failure-overlay'

const setPrimaryGatewayMock = vi.fn()
const reloadMock = vi.fn()
const useInteractiveRegionMock = vi.fn()

vi.mock('@/shared/store/gateway', () => ({
  setPrimaryGateway: (...args: unknown[]) => setPrimaryGatewayMock(...args)
}))

vi.mock('@/companion/interactive-regions', () => ({
  useInteractiveRegion: (...args: unknown[]) => useInteractiveRegionMock(...args)
}))

const makeBootState = (overrides: Partial<DesktopBootState>): DesktopBootState => ({
  error: null,
  fakeMode: false,
  message: '',
  phase: 'pending',
  progress: 0,
  running: false,
  timestamp: 0,
  visible: false,
  ...overrides
})

describe('BootFailureOverlay', () => {
  const originalReload = Object.getOwnPropertyDescriptor(window, 'location')

  beforeEach(() => {
    setPrimaryGatewayMock.mockReset()
    reloadMock.mockReset()
    useInteractiveRegionMock.mockReset()
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...window.location, reload: reloadMock }
    })
  })

  afterEach(() => {
    cleanup()

    if (originalReload) {
      Object.defineProperty(window, 'location', originalReload)
    }

    $desktopBoot.set(makeBootState({}))
  })

  it('renders nothing when the boot phase is not in error', () => {
    $desktopBoot.set(makeBootState({}))
    const { container } = render(<BootFailureOverlay />)
    expect(container.firstChild).toBeNull()
    // Hook still called once (region callback returns null when ref is unset).
    expect(useInteractiveRegionMock).toHaveBeenCalledTimes(1)
  })

  it('renders the failure card with a message when boot errored', () => {
    $desktopBoot.set(makeBootState({ error: 'BOOT_FAIL', message: '测试失败', phase: 'renderer.error' }))
    render(<BootFailureOverlay />)
    expect(screen.getByRole('alertdialog')).toBeTruthy()
    expect(screen.getByText('测试失败')).toBeTruthy()
  })

  it('registers a fullscreen interactive region so Retry stays clickable', () => {
    // The whole point of the CSS + interactive-region fix: without this the
    // sprite window's click-through swallows the Retry click.
    $desktopBoot.set(makeBootState({ error: 'BOOT_FAIL', message: '...', phase: 'renderer.error' }))
    render(<BootFailureOverlay />)

    expect(useInteractiveRegionMock).toHaveBeenCalledTimes(1)
    const [id, , getRect] = useInteractiveRegionMock.mock.calls[0]
    expect(id).toBe('boot-failure')

    // The rect is a compile-time viewport constant — invoking getRect with any
    // ref returns the full window dimensions, so isPointInteractive will
    // resolve a hit anywhere on screen during the failure state.
    const rect = getRect(null as unknown as HTMLElement)
    expect(rect.width).toBe(window.innerWidth)
    expect(rect.height).toBe(window.innerHeight)
    expect(rect.left).toBe(0)
    expect(rect.top).toBe(0)
  })

  it('triggers reload and gateway reset on Retry click', () => {
    $desktopBoot.set(makeBootState({ error: 'BOOT_FAIL', message: '...', phase: 'renderer.error' }))
    render(<BootFailureOverlay />)
    const buttons = screen.getAllByRole('button', { name: '重试' })
    expect(buttons).toHaveLength(1)
    fireEvent.click(buttons[0])
    expect(setPrimaryGatewayMock).toHaveBeenCalledWith(null)
    expect(reloadMock).toHaveBeenCalledTimes(1)
  })
})
