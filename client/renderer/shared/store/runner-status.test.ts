import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// $runnerPhase + hydrateRunnerStatus — IPC plumbing (sync getter + event sub)
interface RunnerStatusListener {
  (ev: { type: string; [k: string]: unknown }): void
}

interface RunnerSpiritagent {
  runnerInvoke: ReturnType<typeof vi.fn>
  runnerGetState: ReturnType<typeof vi.fn>
  onRunnerStatus: ReturnType<typeof vi.fn>
  emit: (ev: { type: string }) => void
}

function installRunnerSpiritagent(
  opts: { initialState?: { phase: string }; getStateRejected?: boolean } = {}
): RunnerSpiritagent {
  const listeners: RunnerStatusListener[] = []
  const runnerInvoke = vi.fn().mockResolvedValue({})

  const runnerGetState = opts.getStateRejected
    ? vi.fn().mockRejectedValue(new Error('IPC down'))
    : vi.fn().mockResolvedValue(opts.initialState ?? { phase: 'idle' })

  const onRunnerStatus = vi.fn((cb: RunnerStatusListener) => {
    listeners.push(cb)

    return () => {
      const i = listeners.indexOf(cb)

      if (i >= 0) {
        listeners.splice(i, 1)
      }
    }
  })

  ;(window as unknown as { spiritagent: unknown }).spiritagent = {
    runnerInvoke,
    runnerGetState,
    onRunnerStatus
  }

  return {
    runnerInvoke,
    runnerGetState,
    onRunnerStatus,
    emit: (ev: { type: string }) => {
      for (const l of listeners) {
        l(ev)
      }
    }
  }
}

describe('runner-status store', () => {
  // Each test gets a fresh module so the module-level `offRunnerStatus`
  // idempotency branch doesn't short-circuit subsequent calls.
  beforeEach(() => {
    vi.resetModules()
  })

  afterEach(() => {
    ;(window as unknown as { spiritagent?: unknown }).spiritagent = undefined
  })

  describe('$runnerReady', () => {
    it('is false when phase is idle', async () => {
      const { $runnerReady: ready } = await import('./runner-status')

      expect(ready.get()).toBe(false)
    })

    it('is true when phase is running', async () => {
      const { $runnerPhase: phase } = await import('./runner-status')
      phase.set('running')
      const { $runnerReady: ready } = await import('./runner-status')

      expect(ready.get()).toBe(true)
    })

    it('flips false again when phase drops to stopped', async () => {
      const { $runnerPhase: phase } = await import('./runner-status')
      phase.set('running')
      const { $runnerReady: ready } = await import('./runner-status')
      expect(ready.get()).toBe(true)

      phase.set('stopped')
      const { $runnerReady: ready2 } = await import('./runner-status')
      expect(ready2.get()).toBe(false)
    })
  })

  describe('hydrateRunnerStatus', () => {
    it('populates the atom from the sync getter on first call', async () => {
      installRunnerSpiritagent({ initialState: { phase: 'running' } })
      const { $runnerPhase: phase, hydrateRunnerStatus: hydrate } = await import('./runner-status')

      await hydrate()

      expect(phase.get()).toBe('running')
    })

    it('defaults to idle when the bridge has not been created yet', async () => {
      installRunnerSpiritagent({ initialState: { phase: 'idle' } })
      const { $runnerPhase: phase, hydrateRunnerStatus: hydrate } = await import('./runner-status')

      await hydrate()

      expect(phase.get()).toBe('idle')
    })

    it('swallows sync-getter rejection and leaves atom at default', async () => {
      installRunnerSpiritagent({ getStateRejected: true })
      const { $runnerPhase: phase, hydrateRunnerStatus: hydrate } = await import('./runner-status')

      await hydrate()

      expect(phase.get()).toBe('idle')
    })

    it('subscribes once — repeated hydrate calls do not stack subscriptions', async () => {
      const spy = installRunnerSpiritagent()
      const { hydrateRunnerStatus: hydrate } = await import('./runner-status')

      await hydrate()
      await hydrate()
      await hydrate()

      expect(spy.onRunnerStatus).toHaveBeenCalledTimes(1)
    })

    it('updates the atom when onRunnerStatus fires `running`', async () => {
      const spy = installRunnerSpiritagent({ initialState: { phase: 'starting' } })
      const { $runnerPhase: phase, hydrateRunnerStatus: hydrate } = await import('./runner-status')

      await hydrate()
      expect(phase.get()).toBe('starting')

      spy.emit({ type: 'running' })
      expect(phase.get()).toBe('running')
    })

    it('updates the atom when onRunnerStatus fires `stopped` / `error`', async () => {
      const spy = installRunnerSpiritagent({ initialState: { phase: 'running' } })
      const { $runnerPhase: phase, hydrateRunnerStatus: hydrate } = await import('./runner-status')

      await hydrate()
      expect(phase.get()).toBe('running')

      spy.emit({ type: 'stopped' })
      expect(phase.get()).toBe('stopped')

      spy.emit({ type: 'error' })
      expect(phase.get()).toBe('stopped')
    })

    it('treats `runner_ready` as `running` (transitions to live state)', async () => {
      const spy = installRunnerSpiritagent({ initialState: { phase: 'starting' } })
      const { $runnerPhase: phase, hydrateRunnerStatus: hydrate } = await import('./runner-status')

      await hydrate()

      spy.emit({ type: 'runner_ready' })
      expect(phase.get()).toBe('running')
    })
  })
})
