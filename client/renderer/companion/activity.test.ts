import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { classifyFocusedApp, startActivityMonitor, stopActivityMonitor } from './activity'

describe('classifyFocusedApp', () => {
  it('returns "unknown" for empty / missing info', () => {
    expect(classifyFocusedApp({})).toBe('unknown')
  })

  it('classifies Windows IDE apps', () => {
    // 不依赖实际检测到的平台，单独测试分类逻辑本身——
    // 平台分支逻辑写在 activity.ts 的 Runner 路径里；这里用合成的 Windows 浏览器环境
    // 演练数据驱动的白名单。
    const originalPlatform = (navigator as Navigator).platform
    Object.defineProperty(navigator, 'platform', { value: 'Win32', configurable: true })

    try {
      expect(classifyFocusedApp({ name: 'Code.exe' })).toBe('ide')
      expect(classifyFocusedApp({ name: 'idea64.exe' })).toBe('ide')
      expect(classifyFocusedApp({ name: 'sublime_text.exe' })).toBe('ide')
      expect(classifyFocusedApp({ name: 'notepad.exe' })).toBe('unknown')
    } finally {
      Object.defineProperty(navigator, 'platform', { value: originalPlatform, configurable: true })
    }
  })

  it('classifies Windows media and reader apps', () => {
    Object.defineProperty(navigator, 'platform', { value: 'Win32', configurable: true })

    try {
      expect(classifyFocusedApp({ name: 'Spotify.exe' })).toBe('music')
      expect(classifyFocusedApp({ name: 'Acrobat.exe' })).toBe('reader')
      expect(classifyFocusedApp({ name: 'chrome.exe' })).toBe('browsing')
      expect(classifyFocusedApp({ name: 'steam.exe' })).toBe('gaming')
    } finally {
      Object.defineProperty(navigator, 'platform', { value: '', configurable: true })
    }
  })

  it('classifies macOS bundle prefixes', () => {
    Object.defineProperty(navigator, 'platform', { value: 'MacIntel', configurable: true })

    try {
      expect(classifyFocusedApp({ bundle: 'com.microsoft.VSCode', name: 'Visual Studio Code' })).toBe('ide')
      expect(classifyFocusedApp({ bundle: 'com.spotify.client', name: 'Spotify' })).toBe('music')
      expect(classifyFocusedApp({ bundle: 'com.adobe.Acrobat', name: 'Adobe Acrobat' })).toBe('reader')
      expect(classifyFocusedApp({ bundle: 'com.valvesoftware.steam', name: 'Steam' })).toBe('gaming')
      expect(classifyFocusedApp({ bundle: 'com.google.Chrome', name: 'Google Chrome' })).toBe('browsing')
    } finally {
      Object.defineProperty(navigator, 'platform', { value: '', configurable: true })
    }
  })

  it('returns "unknown" for unrecognised processes', () => {
    Object.defineProperty(navigator, 'platform', { value: 'Win32', configurable: true })

    try {
      expect(classifyFocusedApp({ name: 'mystery_process.exe' })).toBe('unknown')
    } finally {
      Object.defineProperty(navigator, 'platform', { value: '', configurable: true })
    }
  })
})

// startActivityMonitor — Runner 网关
//
// 网关生命周期现在统一在共享的 `$runnerPhase` atom 里（见
// `@/shared/store/runner-status`）。水合 IPC 的连通性在 runner-status.test.ts 中
// 测过；这里直接驱动 `$runnerPhase` 单独演练 activity.ts 的消费侧逻辑。
import { $runnerPhase } from '@/shared/store/runner-status'

interface ActivitySpiritagent {
  runnerInvoke: ReturnType<typeof vi.fn>
}

function installActivitySpiritagent(): ActivitySpiritagent {
  const runnerInvoke = vi.fn().mockResolvedValue({
    idle_seconds: 0,
    locked: false,
    focused_app: {},
    fullscreen: false
  })

  ;(window as unknown as { spiritagent: unknown }).spiritagent = { runnerInvoke }

  return { runnerInvoke }
}

describe('startActivityMonitor runner-gate', () => {
  beforeEach(() => {
    $runnerPhase.set('idle')
    vi.useFakeTimers()
  })

  afterEach(() => {
    stopActivityMonitor()
    vi.useRealTimers()
    ;(window as unknown as { spiritagent?: unknown }).spiritagent = undefined
  })

  it('does NOT poll while phase stays idle', async () => {
    const { runnerInvoke } = installActivitySpiritagent()

    startActivityMonitor()
    await vi.advanceTimersByTimeAsync(0)
    expect(runnerInvoke).not.toHaveBeenCalled()

    // Even at the 30s tick, runnerReady is false → no-op.
    await vi.advanceTimersByTimeAsync(30_000)
    expect(runnerInvoke).not.toHaveBeenCalled()
  })

  it('kicks a poll the moment phase transitions to running', async () => {
    const { runnerInvoke } = installActivitySpiritagent()

    startActivityMonitor()
    await vi.advanceTimersByTimeAsync(0)
    expect(runnerInvoke).not.toHaveBeenCalled()

    $runnerPhase.set('running')

    await vi.advanceTimersByTimeAsync(0)
    expect(runnerInvoke).toHaveBeenCalledTimes(1)
  })

  it('kicks immediately when phase is already running on subscribe', async () => {
    const { runnerInvoke } = installActivitySpiritagent()

    // 网关在订阅之前就已就绪——模拟 activity.ts 还没观察时 Runner 已进入 running
    // 的场景。nanostore 在订阅时会用当前值触发一次回调。
    $runnerPhase.set('running')

    startActivityMonitor()
    await vi.advanceTimersByTimeAsync(0)
    expect(runnerInvoke).toHaveBeenCalledTimes(1)
  })

  it('keeps polling on the setInterval cadence while phase stays running', async () => {
    const { runnerInvoke } = installActivitySpiritagent()
    $runnerPhase.set('running')

    startActivityMonitor()
    await vi.advanceTimersByTimeAsync(0)
    const initial = runnerInvoke.mock.calls.length

    await vi.advanceTimersByTimeAsync(30_000)
    expect(runnerInvoke.mock.calls.length).toBe(initial + 1)
  })

  it('skips setInterval polls after a stopped transition, resumes on the next 30s tick', async () => {
    const { runnerInvoke } = installActivitySpiritagent()
    $runnerPhase.set('running')

    startActivityMonitor()
    await vi.advanceTimersByTimeAsync(0)
    const beforeStop = runnerInvoke.mock.calls.length

    $runnerPhase.set('stopped')

    // 30s tick with runnerReady=false must NOT issue any new runnerInvoke.
    await vi.advanceTimersByTimeAsync(30_000)
    expect(runnerInvoke.mock.calls.length).toBe(beforeStop)

    $runnerPhase.set('running')

    // 首次轮询已由初次 kick 完成——再次进入 running 只会把 runnerReady 翻回 true。
    // 真正恢复轮询的是下一次 setInterval tick（刻意如此：恢复时不爆发轮询，
    // 只是重新并入 30 秒节拍）。
    await vi.advanceTimersByTimeAsync(0)
    expect(runnerInvoke.mock.calls.length).toBe(beforeStop)

    await vi.advanceTimersByTimeAsync(30_000)
    expect(runnerInvoke.mock.calls.length).toBe(beforeStop + 1)
  })

  it('stopActivityMonitor detaches the subscription and clears the interval', async () => {
    const { runnerInvoke } = installActivitySpiritagent()
    $runnerPhase.set('running')

    startActivityMonitor()
    await vi.advanceTimersByTimeAsync(0)

    stopActivityMonitor()

    // stop 之后的 phase 变化对此 monitor 不应有任何影响。
    $runnerPhase.set('stopped')
    $runnerPhase.set('running')
    const beforeTimer = runnerInvoke.mock.calls.length

    await vi.advanceTimersByTimeAsync(30_000)
    expect(runnerInvoke.mock.calls.length).toBe(beforeTimer)
  })
})
