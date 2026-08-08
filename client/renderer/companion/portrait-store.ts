import { atom } from 'nanostores'

import { isClientErrorIpc } from '@/shared/lib/ipc-error'

import { resolvePortraitUrl } from './avatar-image'

// Resolved data URL of the companion's 2D portrait. Hydrated on app start from
// GET /api/companion/avatar and refreshed on regen / upload. The 3D model is
// independent; the portrait is the visible identity in the chat header and the
// 形象 section of 伙伴设置.
export const $portraitUrl = atom<string | null>(null)

export function setPortraitUrl(url: string | null): void {
  $portraitUrl.set(url)
}

// Resolve a fresh asset_url into a data URL and publish it to the global atom.
// Single entry point for onboarding regen, settings regen / upload, and any
// future surface (e.g. persona retune) that produces a new portrait.
export async function applyPortrait(assetUrl: string | null | undefined): Promise<string | null> {
  const resolved = await resolvePortraitUrl(assetUrl)

  if (resolved) {
    setPortraitUrl(resolved)
  }

  return resolved
}

// Pulls the active portrait from the backend on app start. Called from
// root.tsx once the user authenticates; 404 (no avatar yet during onboarding)
// is expected and leaves the atom null.
export async function hydratePortrait(): Promise<void> {
  try {
    const res = await window.deskagent.api<{ asset_url?: string }>({ path: '/api/companion/avatar' })

    await applyPortrait(res?.asset_url)
  } catch (error) {
    // 4xx (404 = no avatar yet during onboarding, 401 = token expired) are
    // expected; leave the atom null so subscribers show their placeholder.
    // 5xx / network errors surface as console warnings — a silently missing
    // portrait forever is worse than a diagnostic.
    if (!isClientErrorIpc(error)) {
      console.warn('hydratePortrait failed', error)
    }
  }
}
