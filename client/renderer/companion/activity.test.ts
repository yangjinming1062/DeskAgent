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

// ---------------------------------------------------------------------------
// startActivityMonitor — runner-gate (subscribe + sync getter pattern)
// ---------------------------------------------------------------------------

// Each test installs a fresh `window.deskagent` with controllable bridge state.
// `runnerInvoke` is always wired so a poll that DOES fire observes a settled
// promise rather than throwing — keeps the assertions about polling cadence
// independent of probe-result shape.
interface RunnerStatusListener {
  (ev: { type: string; [k: string]: unknown }): void
}

function installDeskagent(
  opts: { initialState?: { phase: string }; getStateRejected?: boolean } = {}
) {
  const listeners: RunnerStatusListener[] = []
  const runnerInvoke = vi.fn().mockResolvedValue({ locked: false, idle_seconds: 0, focused_app: null, fullscreen: false })

  const runnerGetState = opts.getStateRejected
    ? vi.fn().mockRejectedValue(new Error('IPC down'))
    : vi.fn().mockResolvedValue(opts.initialState ?? { phase: 'idle' })

  const onRunnerStatus = vi.fn((cb: RunnerStatusListener) => {
    listeners.push(cb)

    return () => {
      const i = listeners.indexOf(cb)

      if (i >= 0) {listeners.splice(i, 1)}
    }
  })

  ;(window as unknown as { deskagent: unknown }).deskagent = {
    runnerInvoke,
    runnerGetState,
    onRunnerStatus
  }

  return {
    listeners,
    runnerInvoke,
    runnerGetState,
    emit: (ev: { type: string }) => {
      for (const l of listeners) {l(ev)}
    }
  }
}

describe('startActivityMonitor runner-gate', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    stopActivityMonitor()
    vi.useRealTimers()
    ;(window as unknown as { deskagent?: unknown }).deskagent = undefined
  })

  it('does NOT poll when the bridge is not running yet', async () => {
    const spy = installDeskagent({ initialState: { phase: 'idle' } })

    startActivityMonitor()

    // Drain microtasks so the sync runnerGetState().then() resolves.
    await vi.advanceTimersByTimeAsync(0)

    expect(spy.runnerGetState).toHaveBeenCalledTimes(1)
    expect(spy.runnerInvoke).not.toHaveBeenCalled()

    // No fake-timer-driven setInterval tick should fire a poll either — at
    // 30s the timer fires but runnerReady is still false, so pollOnce is a no-op.
    await vi.advanceTimersByTimeAsync(30_000)
    expect(spy.runnerInvoke).not.toHaveBeenCalled()
  })

  it('starts polling as soon as the bridge emits `running`', async () => {
    const spy = installDeskagent({ initialState: { phase: 'starting' } })

    startActivityMonitor()

    await vi.advanceTimersByTimeAsync(0)
    expect(spy.runnerInvoke).not.toHaveBeenCalled()

    spy.emit({ type: 'running' })

    // First poll is async; advance microtasks only — not the 30s timer.
    await vi.advanceTimersByTimeAsync(0)
    expect(spy.runnerInvoke).toHaveBeenCalledTimes(4)
  })

  it('starts polling immediately when the sync getter reports phase=running', async () => {
    // No event ever fires — the bridge was already up before we subscribed.
    const spy = installDeskagent({ initialState: { phase: 'running' } })

    startActivityMonitor()
    await vi.advanceTimersByTimeAsync(0)

    expect(spy.runnerGetState).toHaveBeenCalledTimes(1)
    expect(spy.runnerInvoke).toHaveBeenCalledTimes(4)
  })

  it('keeps polling on the setInterval cadence while runnerReady stays true', async () => {
    const spy = installDeskagent({ initialState: { phase: 'running' } })

    startActivityMonitor()
    await vi.advanceTimersByTimeAsync(0)
    const initial = spy.runnerInvoke.mock.calls.length

    // 30s tick fires one more poll (4 more runnerInvoke calls).
    await vi.advanceTimersByTimeAsync(30_000)
    expect(spy.runnerInvoke.mock.calls.length).toBe(initial + 4)
  })

  it('skips setInterval polls after the bridge stops, resumes on the next 30s tick', async () => {
    const spy = installDeskagent({ initialState: { phase: 'running' } })

    startActivityMonitor()
    await vi.advanceTimersByTimeAsync(0)
    const beforeStop = spy.runnerInvoke.mock.calls.length

    spy.emit({ type: 'stopped' })

    // 30s tick with runnerReady=false must NOT issue any new runnerInvoke.
    await vi.advanceTimersByTimeAsync(30_000)
    expect(spy.runnerInvoke.mock.calls.length).toBe(beforeStop)

    spy.emit({ type: 'running' })

    // firstPollDone is already true from the initial kick — re-emitting
    // `running` only flips runnerReady. The next setInterval tick is what
    // resumes polling (intentional: don't burst polls on recovery, just
    // rejoin the 30s cadence).
    await vi.advanceTimersByTimeAsync(0)
    expect(spy.runnerInvoke.mock.calls.length).toBe(beforeStop)

    await vi.advanceTimersByTimeAsync(30_000)
    expect(spy.runnerInvoke.mock.calls.length).toBe(beforeStop + 4)
  })

  it('stopActivityMonitor detaches the subscription and clears the interval', async () => {
    const spy = installDeskagent({ initialState: { phase: 'running' } })

    startActivityMonitor()
    await vi.advanceTimersByTimeAsync(0)

    stopActivityMonitor()

    // Emitting after stop should have no effect.
    spy.emit({ type: 'running' })
    const beforeTimer = spy.runnerInvoke.mock.calls.length

    await vi.advanceTimersByTimeAsync(30_000)
    expect(spy.runnerInvoke.mock.calls.length).toBe(beforeTimer)
  })

  it('treats runnerGetState() rejection as "not ready" and waits for the event', async () => {
    const spy = installDeskagent({ getStateRejected: true })

    startActivityMonitor()
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(30_000)

    // Rejection → no poll. The bridge recovers and emits `running` — that's
    // what drives the first kick.
    expect(spy.runnerInvoke).not.toHaveBeenCalled()

    spy.emit({ type: 'running' })
    await vi.advanceTimersByTimeAsync(0)
    expect(spy.runnerInvoke).toHaveBeenCalledTimes(4)
  })
})
