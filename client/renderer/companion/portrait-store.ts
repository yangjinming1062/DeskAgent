import { atom } from 'nanostores'

import { isClientErrorIpc } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'

import { resolvePortraitUrl } from './avatar-image'

// Resolved data URL of the companion's 2D portrait. Hydrated on app start from
// GET /api/companion/avatar and refreshed on regen. The 3D model is independent;
// the portrait is the visible identity in the chat header and the 形象 section.
export const $portraitUrl = atom<string | null>(null)
export const $seedFrontUrl = atom<string | null>(null)
export const $seedRightUrl = atom<string | null>(null)
export const $seedBackUrl = atom<string | null>(null)
export const $fullbodyStyle = atom<string>('cel_shading')
export const $fullbodySamples = atom<Record<string, string>>({})

// Active avatar row id — written by hydrate + by every regen that creates a
// fresh row. The 3D pipeline reads the active avatar row server-side, so the
// client only mirrors it for gallery selection.
export const $activeAvatarId = atom<number | null>(null)

export function setPortraitUrl(url: string | null): void {
  $portraitUrl.set(url)
}

export function setSeedFrontUrl(url: string | null): void {
  $seedFrontUrl.set(url)
}

export function setSeedRightUrl(url: string | null): void {
  $seedRightUrl.set(url)
}

export function setSeedBackUrl(url: string | null): void {
  $seedBackUrl.set(url)
}

export function setFullbodyStyle(style: string): void {
  $fullbodyStyle.set(style)
}

export function setFullbodySamples(samples: Record<string, string>): void {
  $fullbodySamples.set(samples)
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

// Resolve a fresh asset_url into a data URL. Publishes to the global
// $portraitUrl atom; returns the resolved URL.
export async function applyPortrait(urls: PortraitUrls): Promise<{ avatar: string | null; seedFront: string | null }> {
  const avatar = urls.assetUrl === undefined ? null : await resolvePortraitUrl(urls.assetUrl)
  const seedFront = urls.seedFrontUrl === undefined ? null : await resolvePortraitUrl(urls.seedFrontUrl)
  const seedRight = urls.seedRightUrl === undefined ? null : await resolvePortraitUrl(urls.seedRightUrl)
  const seedBack = urls.seedBackUrl === undefined ? null : await resolvePortraitUrl(urls.seedBackUrl)

  if (avatar) {
    setPortraitUrl(avatar)
  }

  if (seedFront !== null) {
    setSeedFrontUrl(seedFront)
  }

  if (seedRight !== null) {
    setSeedRightUrl(seedRight)
  }

  if (seedBack !== null) {
    setSeedBackUrl(seedBack)
  }

  if (urls.id != null) {
    setActiveAvatarId(urls.id)
  }

  return { avatar, seedFront }
}

// Pulls the active portrait from the backend on app start. Called from
// root.tsx once the user authenticates; 404 (no avatar yet during onboarding)
// is expected and leaves the atoms null.
export async function hydratePortrait(): Promise<void> {
  try {
    const res = await window.spiritagent.api<{
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
      log.warn('portrait', 'hydratePortrait failed', error)
    }
  }
}

// Pulls the avatar history from the backend on app start. Without this, the
// gallery thumbnails are empty after every restart — the user only sees the
// active avatar and has to regenerate to get visual alternatives.
//
// History is append-order on the client (oldest first), but the backend
// returns desc order; reverse so pushPortraitEntry lays them out chronologically.
export async function hydratePortraitHistory(): Promise<void> {
  try {
    const res = await window.spiritagent.api<{
      history: Array<{
        id: number
        asset_url: string
      }>
    }>({
      path: '/api/companion/avatar/history'
    })

    const items = [...(res?.history ?? [])].reverse()

    // Clear any stale local history before re-populating — a partial hydrate
    // would otherwise leave the user seeing fewer entries than the server has.
    $portraitHistory.set([])
    $portraitSelectedIdx.set(0)

    for (const item of items) {
      const portraitUrl = await resolvePortraitUrl(item.asset_url)

      pushPortraitEntry({
        portraitUrl,
        avatarId: item.id
      })
    }

    const activeId = $activeAvatarId.get()

    if (activeId != null) {
      const activeIdx = $portraitHistory.get().findIndex(e => e.avatarId === activeId)

      if (activeIdx >= 0) {
        $portraitSelectedIdx.set(activeIdx)
      }
    }
  } catch (error) {
    if (!isClientErrorIpc(error)) {
      log.warn('portrait', 'hydratePortraitHistory failed', error)
    }
  }
}

export async function selectAvatar(avatarId: number): Promise<boolean> {
  try {
    await window.spiritagent.api({
      path: `/api/companion/avatar/${avatarId}/select`,
      method: 'PUT'
    })
    setActiveAvatarId(avatarId)

    return true
  } catch (error) {
    if (!isClientErrorIpc(error)) {
      log.warn('portrait', 'selectAvatar failed', error)
    }

    return false
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

export interface PortraitEntry {
  portraitUrl: string | null
  avatarId: number | null
}

const _MAX_HISTORY = 5

export const $portraitHistory = atom<PortraitEntry[]>([])
export const $portraitSelectedIdx = atom<number>(0)

export function pushPortraitEntry(entry: PortraitEntry): void {
  const current = $portraitHistory.get()
  const next = [...current, entry]

  if (next.length > _MAX_HISTORY) {
    next.shift()
  }

  $portraitHistory.set(next)
  $portraitSelectedIdx.set(next.length - 1)
}

export function selectPortraitEntry(idx: number): void {
  const current = $portraitHistory.get()

  if (idx >= 0 && idx < current.length) {
    $portraitSelectedIdx.set(idx)
  }
}

export function clearPortraitHistory(): void {
  $portraitHistory.set([])
  $portraitSelectedIdx.set(0)
}
