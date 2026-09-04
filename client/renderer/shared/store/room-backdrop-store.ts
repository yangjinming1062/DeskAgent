// 房间背景状态机：none → pending → ready；换装 invalidated → pending → ready；
// 失败 failed → 重试入口。本期直连后端：
// - GET /api/companion/room 冷启动水合
// - WS `companion.room.ready` / `failed` / `invalidated` / `progress` 增量更新
// - POST /api/companion/room/generate 触发生成（由衣橱里的"换一间"调用）

import { atom } from 'nanostores'

import { authedApi } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { registerStorageClearHandler } from '@/shared/lib/storage'

export type BackdropStatus = 'failed' | 'none' | 'pending' | 'ready'

export interface ActiveBackdrop {
  brief: string
  id: string
  origin?: string
  outfitFingerprint?: string
  prompt?: string
  status: Exclude<BackdropStatus, 'none'>
  url: string
}

interface RoomBackdropWire {
  id: number | string
  status: 'pending' | 'ready' | 'failed' | 'superseded'
  origin?: string
  brief?: string
  prompt?: string
  url?: string
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

export const $backdropStatus = atom<BackdropStatus>('none')
export const $activeBackdrop = atom<null | ActiveBackdrop>(null)
export const $pendingBackdrop = atom<null | ActiveBackdrop>(null)

function resetRoomBackdrop(): void {
  $backdropStatus.set('none')
  $activeBackdrop.set(null)
  $pendingBackdrop.set(null)
}

registerStorageClearHandler(resetRoomBackdrop)

async function applyRoomState(state: RoomStateWire): Promise<void> {
  $backdropStatus.set(deriveStatus(state))

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
      history: [],
      pending: null,
      policy: 'llm_may_replace'
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
