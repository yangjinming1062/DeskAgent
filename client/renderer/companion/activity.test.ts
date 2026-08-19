import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { classifyFocusedApp, startActivityMonitor, stopActivityMonitor } from './activity'

describe('classifyFocusedApp', () => {
  it('returns "unknown" for empty / missing info', () => {
    expect(classifyFocusedApp({})).toBe('unknown')
  })

  it('classifies Windows IDE apps', () => {
    // Test the underlying classification regardless of detected platform —
    // the platform gate lives in the activity.ts runner; here we exercise
    // the data-driven allowlist via a synthetic Windows browser env.
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

// startActivityMonitor — runner-gate
//
// The bridge lifecycle now lives in the shared `$runnerPhase` atom (see
// `@/shared/store/runner-status`). Hydration IPC plumbing is tested in
// runner-status.test.ts; here we drive `$runnerPhase` directly to exercise
// activity.ts's consumer-side logic in isolation.
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

    // Bridge already up before subscribe — simulates a runner that reached
    // running while activity.ts wasn't watching. nanostore fires the
    // callback once with the current value on subscribe.
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

    // firstPollDone is already true from the initial kick — re-entering
    // running only flips runnerReady. The next setInterval tick is what
    // resumes polling (intentional: don't burst polls on recovery, just
    // rejoin the 30s cadence).
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

    // Phase changes after stop must have no effect on this monitor.
    $runnerPhase.set('stopped')
    $runnerPhase.set('running')
    const beforeTimer = runnerInvoke.mock.calls.length

    await vi.advanceTimersByTimeAsync(30_000)
    expect(runnerInvoke.mock.calls.length).toBe(beforeTimer)
  })
})
