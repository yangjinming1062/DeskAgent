// Public surface of renderer/companion. Imports outside this barrel go via
// `@/companion/<module>`/<file>` directly. The sprite-window entry (app.tsx)
// mounts CompanionRoot; everything else stays internal.

export { $desktopBoot, completeDesktopBoot, failDesktopBoot } from './boot-store'
export { $chatOpen, setChatOpen } from './chat-store'

export {
  $companionLifecycle,
  $effectiveTier,
  $effectiveTierOverride,
  $spriteState,
  $userPreferredTier,
  setCompanionLifecycle,
  setDisturbanceTier,
  setSpriteState
} from './companion-store'

export { handleCompanionEvent } from './events'
export { CompanionRoot } from './root'
export {
  $defaultScale,
  $homePosition,
  $spatialLocale,
  $spatialLocomotion,
  $spatialPos,
  $spatialScale,
  initSpatial,
  type Locomotion,
  setDefaultScale,
  setLocale,
  type SpatialLocale
} from './spatial'
