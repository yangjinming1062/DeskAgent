import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $runnerPhase, $runnerReady, hydrateRunnerStatus, teardownRunnerStatus } from './runner-status'

// ---------------------------------------------------------------------------
// $runnerPhase + hydrateRunnerStatus — IPC plumbing (sync getter + event sub)
// ---------------------------------------------------------------------------

interface RunnerStatusListener {
  (ev: { type: string; [k: string]: unknown }): void
}

interface RunnerDeskagent {
  runnerInvoke: ReturnType<typeof vi.fn>
  runnerGetState: ReturnType<typeof vi.fn>
  onRunnerStatus: ReturnType<typeof vi.fn>
  emit: (ev: { type: string }) => void
}

function installRunnerDeskagent(opts: { initialState?: { phase: string }; getStateRejected?: boolean } = {}): RunnerDeskagent {
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

  ;(window as unknown as { deskagent: unknown }).deskagent = {
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
  beforeEach(() => {
    $runnerPhase.set('idle')
  })

  afterEach(() => {
    teardownRunnerStatus()
    ;(window as unknown as { deskagent?: unknown }).deskagent = undefined
  })

  describe('$runnerReady', () => {
    it('is false when phase is idle', () => {
      $runnerPhase.set('idle')
      expect($runnerReady.get()).toBe(false)
    })

    it('is true when phase is running', () => {
      $runnerPhase.set('running')
      expect($runnerReady.get()).toBe(true)
    })

    it('flips false again when phase drops to stopped', () => {
      $runnerPhase.set('running')
      expect($runnerReady.get()).toBe(true)

      $runnerPhase.set('stopped')
      expect($runnerReady.get()).toBe(false)
    })
  })

  describe('hydrateRunnerStatus', () => {
    it('populates the atom from the sync getter on first call', async () => {
      installRunnerDeskagent({ initialState: { phase: 'running' } })

      await hydrateRunnerStatus()

      expect($runnerPhase.get()).toBe('running')
    })

    it('defaults to idle when the bridge has not been created yet', async () => {
      installRunnerDeskagent({ initialState: { phase: 'idle' } })

      await hydrateRunnerStatus()

      expect($runnerPhase.get()).toBe('idle')
    })

    it('swallows sync-getter rejection and leaves atom at default', async () => {
      installRunnerDeskagent({ getStateRejected: true })

      await hydrateRunnerStatus()

      expect($runnerPhase.get()).toBe('idle')
    })

    it('subscribes once — repeated hydrate calls do not stack subscriptions', async () => {
      const spy = installRunnerDeskagent()

      await hydrateRunnerStatus()
      await hydrateRunnerStatus()
      await hydrateRunnerStatus()

      expect(spy.onRunnerStatus).toHaveBeenCalledTimes(1)
    })

    it('updates the atom when onRunnerStatus fires `running`', async () => {
      const spy = installRunnerDeskagent({ initialState: { phase: 'starting' } })

      await hydrateRunnerStatus()
      expect($runnerPhase.get()).toBe('starting')

      spy.emit({ type: 'running' })
      expect($runnerPhase.get()).toBe('running')
    })

    it('updates the atom when onRunnerStatus fires `stopped` / `error`', async () => {
      const spy = installRunnerDeskagent({ initialState: { phase: 'running' } })

      await hydrateRunnerStatus()
      expect($runnerPhase.get()).toBe('running')

      spy.emit({ type: 'stopped' })
      expect($runnerPhase.get()).toBe('stopped')

      spy.emit({ type: 'error' })
      expect($runnerPhase.get()).toBe('stopped')
    })

    it('treats `runner_ready` as `running` (transitions to live state)', async () => {
      const spy = installRunnerDeskagent({ initialState: { phase: 'starting' } })

      await hydrateRunnerStatus()

      spy.emit({ type: 'runner_ready' })
      expect($runnerPhase.get()).toBe('running')
    })

    it('teardownRunnerStatus detaches the onRunnerStatus subscription', async () => {
      const spy = installRunnerDeskagent({ initialState: { phase: 'starting' } })

      await hydrateRunnerStatus()
      expect(spy.onRunnerStatus).toHaveBeenCalledTimes(1)

      teardownRunnerStatus()

      // After teardown, a fresh hydrate should subscribe again (the previous
      // unsubscribe was called and we should re-attach on next hydrate).
      await hydrateRunnerStatus()
      expect(spy.onRunnerStatus).toHaveBeenCalledTimes(2)
    })
  })
})