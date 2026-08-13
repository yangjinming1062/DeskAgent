import { atom } from 'nanostores'

import { isClientErrorIpc } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'

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

  if (urls.seedFrontUrl || urls.seedRightUrl || urls.seedBackUrl) {
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
    const res = await window.deskagent.api<{
      history: Array<{
        id: number
        asset_url: string
        seed_front_url?: string | null
        seed_right_url?: string | null
        seed_back_url?: string | null
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
      const [portraitUrl, front, right, back] = await Promise.all([
        resolvePortraitUrl(item.asset_url),
        resolvePortraitUrl(item.seed_front_url),
        resolvePortraitUrl(item.seed_right_url),
        resolvePortraitUrl(item.seed_back_url)
      ])

      pushPortraitEntry({
        portraitUrl,
        avatarId: item.id,
        seedUrls: front || right || back ? { front, right, back } : null
      })
    }
  } catch (error) {
    if (!isClientErrorIpc(error)) {
      log.warn('portrait', 'hydratePortraitHistory failed', error)
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

export interface PortraitEntry {
  portraitUrl: string | null
  avatarId: number | null
  seedUrls: SeedUrls | null
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

// Fullbody regen lives on the same avatar row as the original bust — pushing a
// fresh entry per view would duplicate the same avatar in the gallery. Merge
// into the existing entry by avatarId when one matches; fall back to push on
// a new avatar (e.g. bust regen creates a fresh row id).
export function commitPortraitEntry(entry: PortraitEntry): void {
  const current = $portraitHistory.get()
  const idx = current.findIndex(e => e.avatarId != null && entry.avatarId != null && e.avatarId === entry.avatarId)

  if (idx < 0) {
    pushPortraitEntry(entry)

    return
  }

  const updated = [...current]
  updated[idx] = {
    portraitUrl: entry.portraitUrl ?? current[idx].portraitUrl,
    avatarId: current[idx].avatarId,
    seedUrls: entry.seedUrls
  }
  $portraitHistory.set(updated)
  $portraitSelectedIdx.set(idx)
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
