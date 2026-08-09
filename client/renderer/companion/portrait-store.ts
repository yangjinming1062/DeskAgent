import { atom } from 'nanostores'

import { isClientErrorIpc } from '@/shared/lib/ipc-error'

import { resolvePortraitUrl } from './avatar-image'

// Resolved data URL of the companion's 2D portrait. Hydrated on app start from
// GET /api/companion/avatar and refreshed on regen. The 3D model is independent;
// the portrait is the visible identity in the chat header and the 形象 section.
export const $portraitUrl = atom<string | null>(null)

export interface SeedUrls {
  front: string | null
  right: string | null
  back: string | null
}

// Resolved data URLs of full-body multiview seeds (front, right, back).
export const $seedUrls = atom<SeedUrls | null>(null)

// Active avatar row id — written by hydrate + by every regen that creates a
// fresh row. Two-step flows read this to point the fullbody step at the
// just-confirmed avatar without an extra GET /avatar round-trip.
export const $activeAvatarId = atom<number | null>(null)

export function setPortraitUrl(url: string | null): void {
  $portraitUrl.set(url)
}

export function setSeedUrls(urls: SeedUrls | null): void {
  $seedUrls.set(urls)
}

export function setActiveAvatarId(id: number | null): void {
  $activeAvatarId.set(id)
}

export interface PortraitUrls {
  assetUrl?: string | null
  seedFrontUrl?: string | null
  seedRightUrl?: string | null
  seedBackUrl?: string | null
  id?: number | null
}

// Resolve fresh asset_url / seed multiview URLs into data URLs. Publishes to
// global $portraitUrl and $seedUrls atoms; returns resolved URLs.
export async function applyPortrait(urls: PortraitUrls): Promise<{ avatar: string | null; seeds: SeedUrls | null }> {
  const avatar = urls.assetUrl === undefined ? null : await resolvePortraitUrl(urls.assetUrl)

  if (avatar) {
    setPortraitUrl(avatar)
  }

  let seeds: SeedUrls | null = null

  if (urls.seedFrontUrl !== undefined || urls.seedRightUrl !== undefined || urls.seedBackUrl !== undefined) {
    const [front, right, back] = await Promise.all([
      urls.seedFrontUrl ? resolvePortraitUrl(urls.seedFrontUrl) : Promise.resolve(null),
      urls.seedRightUrl ? resolvePortraitUrl(urls.seedRightUrl) : Promise.resolve(null),
      urls.seedBackUrl ? resolvePortraitUrl(urls.seedBackUrl) : Promise.resolve(null)
    ])

    seeds = { front, right, back }
    setSeedUrls(seeds)
  }

  if (urls.id != null) {
    setActiveAvatarId(urls.id)
  }

  return { avatar, seeds }
}

// Pulls the active portrait from the backend on app start. Called from
// root.tsx once the user authenticates; 404 (no avatar yet during onboarding)
// is expected and leaves the atoms null.
export async function hydratePortrait(): Promise<void> {
  try {
    const res = await window.deskagent.api<{
      id?: number
      asset_url?: string
      seed_front_url?: string
      seed_right_url?: string
      seed_back_url?: string
    }>({
      path: '/api/companion/avatar'
    })

    await applyPortrait({
      id: res?.id,
      assetUrl: res?.asset_url,
      seedFrontUrl: res?.seed_front_url,
      seedRightUrl: res?.seed_right_url,
      seedBackUrl: res?.seed_back_url
    })
  } catch (error) {
    if (!isClientErrorIpc(error)) {
      console.warn('hydratePortrait failed', error)
    }
  }
}

// Free-text feedback the user typed before pressing "重新生成". Shared across
// every surface that exposes the regenerate flow (onboarding / 伙伴设置 /
// 重新对话微调性格 / 角色 inline 编辑) so a half-typed draft survives the
// user closing one panel and reopening another. Cleared on each successful
// regenerate by useRegeneratePortrait.
export const $regenFeedback = atom<string>('')

export function setRegenFeedback(value: string): void {
  $regenFeedback.set(value)
}

export function clearRegenFeedback(): void {
  $regenFeedback.set('')
}
