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
export {
  $portraitUrl,
  $regenFeedback,
  applyPortrait,
  clearRegenFeedback,
  hydratePortrait,
  hydratePortraitHistory,
  selectAvatar,
  setPortraitUrl,
  setRegenFeedback
} from './portrait-store'
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

export {
  useRegeneratePortrait,
  type UseRegeneratePortraitOptions,
  type UseRegeneratePortraitResult
} from './use-regenerate-portrait'
