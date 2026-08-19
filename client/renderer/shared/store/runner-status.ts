import { atom, computed } from 'nanostores'

import type { DesktopRunnerPhase, DesktopRunnerStatusEvent } from '@/shared/types/global'

// Single source of truth for "is the runner bridge live?". Mirrors the
// hydrateAuth + applyAuthBroadcast pattern: one IPC sync getter covers the
// "bridge already running before we subscribed" case (Electron IPC has no
// event replay), and a single subscription fans future transitions into the
// atom. Consumers read $runnerReady for the boolean gate and subscribe to
// $runnerPhase for transition reactions — no per-consumer sync-getter dance
// needed. See companion/activity.ts and hub/settings/speech-settings.tsx.
export const $runnerPhase = atom<DesktopRunnerPhase>('idle')

export const $runnerReady = computed($runnerPhase, phase => phase === 'running')

let offRunnerStatus: (() => void) | null = null

export async function hydrateRunnerStatus(): Promise<void> {
  const desktop = window.spiritagent

  // Sync getter first — closes the "subscribed too late, missed the initial
  // running event" window. If the bridge hasn't been created yet the
  // handler returns { phase: 'idle' }, which is a valid early answer.
  try {
    const state = await desktop.runnerGetState?.()

    if (state?.phase) {
      $runnerPhase.set(state.phase)
    }
  } catch {
    // Bridge probe failed (older preload / IPC transport error). The
    // subscription below is the fallback path.
  }

  // Future transitions. Idempotent: re-calling hydrate after the
  // subscription is already attached just re-runs the sync getter.
  if (offRunnerStatus) {
    return
  }

  offRunnerStatus =
    desktop.onRunnerStatus?.((ev: DesktopRunnerStatusEvent) => {
      if (ev.type === 'running' || ev.type === 'runner_ready') {
        $runnerPhase.set('running')
      } else if (ev.type === 'stopped' || ev.type === 'error') {
        $runnerPhase.set('stopped')
      }
    }) ?? null
}
