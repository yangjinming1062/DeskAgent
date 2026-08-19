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
    // 钩子仍然调用一次（ref 未设置时 region 回调返回 null）。
    expect(useInteractiveRegionMock).toHaveBeenCalledTimes(1)
  })

  it('renders the failure card with a message when boot errored', () => {
    $desktopBoot.set(makeBootState({ error: 'BOOT_FAIL', message: '测试失败', phase: 'renderer.error' }))
    render(<BootFailureOverlay />)
    expect(screen.getByRole('alertdialog')).toBeTruthy()
    expect(screen.getByText('测试失败')).toBeTruthy()
  })

  it('registers a fullscreen interactive region so Retry stays clickable', () => {
    // 这正是 CSS + interactive-region 修复的核心——没有这一步，
    // 精灵窗口的鼠标穿透会吞掉 Retry 点击。
    $desktopBoot.set(makeBootState({ error: 'BOOT_FAIL', message: '...', phase: 'renderer.error' }))
    render(<BootFailureOverlay />)

    expect(useInteractiveRegionMock).toHaveBeenCalledTimes(1)
    const [id, , getRect] = useInteractiveRegionMock.mock.calls[0]
    expect(id).toBe('boot-failure')

    // rect 是编译期视口常量——用任意 ref 调用 getRect 都返回整个窗口的尺寸，
    // 因此失败态下 isPointInteractive 在屏幕上任何位置都会判定为命中。
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
