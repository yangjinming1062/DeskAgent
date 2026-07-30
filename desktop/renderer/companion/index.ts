// Public surface of renderer/companion. Imports outside this barrel go via
// `@/companion/<module>`/<file>` directly. The sprite-window entry (app.tsx)
// mounts CompanionRoot; everything else stays internal.

export { $desktopBoot, completeDesktopBoot, failDesktopBoot } from './boot-store'
export { $chatOpen, setChatOpen } from './chat-store'

export {
  $companionLifecycle,
  $disturbanceTier,
  $spritePosition,
  $spriteState,
  setCompanionLifecycle,
  setDisturbanceTier,
  setSpritePosition,
  setSpriteState
} from './companion-store'

export { handleCompanionEvent } from './events'
export { CompanionRoot } from './root'
