import { atom } from 'nanostores'

import { log } from '@/shared/lib/log'

import type { PowerProfile } from './PowerProfile'
import type { EngineBackendKind } from './types'

// Render-engine observability: which fallback tier actually booted, the live
// power profile, and the measured frame rate. Consumed by the developer
// overlay; profile transitions additionally land in the desktop log file so
// power regressions are diagnosable from logs alone.

export const $rendererBackend = atom<EngineBackendKind | null>(null)
export const $powerProfile = atom<PowerProfile>('active')
export const $engineFps = atom(0)

let lastLoggedProfile: PowerProfile | null = null

export function reportBackend(kind: EngineBackendKind): void {
  $rendererBackend.set(kind)
}

export function reportFrameStats(profile: PowerProfile, fps: number): void {
  $powerProfile.set(profile)
  $engineFps.set(fps)

  if (profile !== lastLoggedProfile) {
    lastLoggedProfile = profile
    log.info('engine', `power profile -> ${profile} (observed ${fps.toFixed(0)} fps)`)
  }
}
