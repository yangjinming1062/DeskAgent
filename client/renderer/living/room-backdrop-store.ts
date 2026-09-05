// 房间背景状态机：none → pending → ready；换装 invalidated → pending → ready；
// 失败 failed → 重试入口。

import { atom } from 'nanostores'

import { authedApi } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import { notify } from '@/shared/store/notifications'

export type BackdropStatus = 'failed' | 'none' | 'pending' | 'ready'

export type RoomPolicy = 'llm_may_replace' | 'locked'

export interface ActiveBackdrop {
  brief: string
  id: string
  origin?: string
  outfitFingerprint?: string
  prompt?: string
  status: Exclude<BackdropStatus, 'none'>
  url: string
}

export interface RoomHistoryEntry {
  brief: string
  id: string
  origin?: string
  outfitFingerprint?: string
  prompt?: string
  thumbnailUrl: string
}

interface RoomBackdropWire {
  id: number | string
  status: 'pending' | 'ready' | 'failed' | 'superseded'
  origin?: string
  brief?: string
  prompt?: string
  url?: string
  thumbnail_url?: string
  outfit_fingerprint?: string
}

interface RoomStateWire {
  active: RoomBackdropWire | null
  history?: RoomBackdropWire[]
  pending: RoomBackdropWire | null
  policy?: string
}

async function resolveBackdropUrl(url?: string): Promise<string> {
  if (!url) {
    return ''
  }

  if (url.startsWith('data:') || url.startsWith('http:') || url.startsWith('https:')) {
    return url
  }

  if (typeof window !== 'undefined' && window.spiritagent?.apiAsset) {
    try {
      const resolved = await window.spiritagent.apiAsset({ url })

      return resolved || url
    } catch {
      return url
    }
  }

  return url
}

async function toActiveBackdrop(w: RoomBackdropWire): Promise<null | ActiveBackdrop> {
  if (w.status !== 'ready' || !w.url) {
    return null
  }

  const url = await resolveBackdropUrl(w.url)

  return {
    brief: w.brief ?? '',
    id: String(w.id),
    origin: w.origin,
    outfitFingerprint: w.outfit_fingerprint,
    prompt: w.prompt,
    status: 'ready',
    url
  }
}

async function toHistoryEntry(w: RoomBackdropWire): Promise<RoomHistoryEntry | null> {
  if (w.status !== 'ready') {
    return null
  }

  const url = await resolveBackdropUrl(w.thumbnail_url ?? w.url)

  if (!url) {
    return null
  }

  return {
    brief: w.brief ?? '',
    id: String(w.id),
    origin: w.origin,
    outfitFingerprint: w.outfit_fingerprint,
    prompt: w.prompt,
    thumbnailUrl: url
  }
}

function deriveStatus(state: RoomStateWire): BackdropStatus {
  if (state.active && state.active.status === 'ready') {
    return 'ready'
  }

  if (state.pending && state.pending.status === 'pending') {
    return 'pending'
  }

  if (state.active && state.active.status === 'failed') {
    return 'failed'
  }

  return 'none'
}

function normalizePolicy(raw: unknown): RoomPolicy {
  return raw === 'locked' ? 'locked' : 'llm_may_replace'
}

export const $backdropStatus = atom<BackdropStatus>('none')
export const $activeBackdrop = atom<null | ActiveBackdrop>(null)
export const $pendingBackdrop = atom<null | ActiveBackdrop>(null)
export const $roomHistory = atom<RoomHistoryEntry[]>([])
export const $roomPolicy = atom<RoomPolicy>('llm_may_replace')

function resetRoomBackdrop(): void {
  $backdropStatus.set('none')
  $activeBackdrop.set(null)
  $pendingBackdrop.set(null)
  $roomHistory.set([])
  $roomPolicy.set('llm_may_replace')
}

registerStorageClearHandler(resetRoomBackdrop)

async function applyRoomState(state: Partial<RoomStateWire> & Pick<RoomStateWire, 'active'>): Promise<void> {
  $backdropStatus.set(deriveStatus(state as RoomStateWire))

  // policy / history 只在水合（GET）或用户主动操作后才回写；WS 单事件没带就不覆盖，
  // 否则会静默清掉用户已经设置过的回滚历史或锁定政策。
  if (state.policy !== undefined) {
    $roomPolicy.set(normalizePolicy(state.policy))
  }

  if (state.active) {
    const active = await toActiveBackdrop(state.active)
    $activeBackdrop.set(active)
  } else {
    $activeBackdrop.set(null)
  }

  if (state.pending) {
    const pending = await toActiveBackdrop(state.pending)
    $pendingBackdrop.set(
      pending ?? {
        brief: state.pending.brief ?? '',
        id: String(state.pending.id),
        origin: state.pending.origin,
        outfitFingerprint: state.pending.outfit_fingerprint,
        prompt: state.pending.prompt,
        status: 'pending',
        url: ''
      }
    )
  } else {
    $pendingBackdrop.set(null)
  }

  if (Array.isArray(state.history)) {
    const history: RoomHistoryEntry[] = []

    for (const entry of state.history) {
      const item = await toHistoryEntry(entry)

      if (item) {
        history.push(item)
      }
    }

    $roomHistory.set(history.slice(0, 5))
  }
}

// 冷启动水合：拉一次完整房间态。失败保留 none 状态（玻璃底 + 占位）。
export function hydrateRoomBackdrop(): void {
  void authedApi<RoomStateWire>({ path: '/api/companion/room' }).then(result => {
    if (!result.ok) {
      if (result.reason === 'err') {
        log.warn('room', 'hydrate failed:', result.error)
      }

      return
    }

    if (result.value) {
      void applyRoomState(result.value)
    }
  })
}

export async function regenerateRoom(): Promise<void> {
  const result = await authedApi({
    body: { intent: 'rebuild' },
    method: 'POST',
    path: '/api/companion/room/generate'
  })

  if (!result.ok) {
    if (result.reason === 'err') {
      notify({ kind: 'warning', message: '换个房间失败了…过会儿再试试' })
    }

    return
  }

  $backdropStatus.set('pending')
}

export async function rollbackRoom(backdropId: string): Promise<void> {
  const id = Number.parseInt(backdropId, 10)

  if (Number.isNaN(id)) {
    return
  }

  const result = await authedApi({
    body: { backdrop_id: id },
    method: 'POST',
    path: '/api/companion/room/activate'
  })

  if (!result.ok) {
    if (result.reason === 'err') {
      notify({ kind: 'warning', message: '回滚失败…历史房间可能已失效' })
    }

    return
  }

  notify({ kind: 'success', message: '已换回之前的房间' })
  void hydrateRoomBackdrop()
}

export async function setRoomPolicy(policy: RoomPolicy): Promise<void> {
  const result = await authedApi({
    body: { policy },
    method: 'PATCH',
    path: '/api/companion/room/policy'
  })

  if (!result.ok) {
    if (result.reason === 'err') {
      notify({ kind: 'warning', message: '锁定设置没改成功' })
    }

    return
  }

  $roomPolicy.set(policy)
}

// WS 事件入口：handleCompanionEvent 在 events.ts 调用此处。
export function onBackdropEvent(event: { payload?: unknown; type: string }): void {
  const p = (event.payload ?? {}) as Partial<RoomBackdropWire> & {
    active_backdrop_id?: number | string
    backdrop_id?: number | string
    reason?: string
    stage?: string
    utterance?: string
  }

  if (event.type === 'companion.room.ready') {
    if (!p.id || !p.url) {
      return
    }

    // WS payload 不带 history / policy：保持已水合的回滚历史与锁定政策，
    // 只更新 active backdrop。下次 hydrateRoomBackdrop() 会把 history 与 policy 重新对账。
    void applyRoomState({
      active: {
        brief: p.brief,
        id: p.id,
        origin: p.origin,
        outfit_fingerprint: p.outfit_fingerprint,
        prompt: p.prompt,
        status: 'ready',
        url: p.url
      },
      pending: null
    })

    return
  }

  if (event.type === 'companion.room.invalidated') {
    // 换装或回滚：active 保留到新图 ready；这里只切到 pending 占位。
    $backdropStatus.set('pending')

    return
  }

  if (event.type === 'companion.room.progress') {
    $backdropStatus.set('pending')

    return
  }

  if (event.type === 'companion.room.failed') {
    $backdropStatus.set('failed')
  }
}
