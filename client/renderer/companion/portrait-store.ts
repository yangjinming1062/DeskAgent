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

interface PortraitUrls {
  assetUrl?: string | null
  seedUrl?: string | null
}

// Resolve fresh asset_url / seed_url into data URLs. Publishes the avatar to
// the global $portraitUrl atom (consumed by chat-dock + settings); returns
// both resolved URLs so callers can mirror to local state (e.g. onboarding
// paired display).
export async function applyPortrait(urls: PortraitUrls): Promise<{ avatar: string | null; seed: string | null }> {
  const avatar = await resolvePortraitUrl(urls.assetUrl)

  if (avatar) {
    setPortraitUrl(avatar)
  }

  const seed = await resolvePortraitUrl(urls.seedUrl)

  return { avatar, seed }
}

// Pulls the active portrait from the backend on app start. Called from
// root.tsx once the user authenticates; 404 (no avatar yet during onboarding)
// is expected and leaves the atom null.
export async function hydratePortrait(): Promise<void> {
  try {
    const res = await window.deskagent.api<{ asset_url?: string; seed_url?: string }>({
      path: '/api/companion/avatar'
    })

    await applyPortrait({ assetUrl: res?.asset_url, seedUrl: res?.seed_url })
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
