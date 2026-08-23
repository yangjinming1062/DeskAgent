import { atom } from 'nanostores'

import { isClientErrorIpc } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'

import { resolvePortraitUrl } from './avatar-image'

// 伙伴 2D 头像的解析后 data URL。启动时从 GET /api/companion/avatar 水合，
// 每次重生后刷新。3D 模型独立；这里管的是聊天头部和「形象」区的可见身份。
export const $portraitUrl = atom<string | null>(null)
export const $supportsMultiview = atom<boolean>(false)

// 当前 avatar 行 id——由 hydrate 与每次创建新行的重生写入。
// 3D 流水线是在服务端读取当前 avatar 行，所以这里只是为画廊选择做镜像。
export const $activeAvatarId = atom<number | null>(null)

export function setPortraitUrl(url: string | null): void {
  $portraitUrl.set(url)
}

export function setSupportsMultiview(supported: boolean): void {
  $supportsMultiview.set(supported)
}

export function setActiveAvatarId(id: number | null): void {
  $activeAvatarId.set(id)
}

export interface PortraitUrls {
  assetUrl?: string | null
  seedFrontUrl?: string | null
  seedBackUrl?: string | null
  id?: number | null
}

// 把新拿到的 asset_url 与正面/背面种子解析成 data URL。asset_url 写入全局 $portraitUrl；
export async function applyPortrait(
  urls: PortraitUrls
): Promise<{ avatar: string | null; seedBack: string | null; seedFront: string | null }> {
  const avatar = urls.assetUrl === undefined ? null : await resolvePortraitUrl(urls.assetUrl)
  const seedFront = urls.seedFrontUrl === undefined ? null : await resolvePortraitUrl(urls.seedFrontUrl)
  const seedBack = urls.seedBackUrl === undefined ? null : await resolvePortraitUrl(urls.seedBackUrl)

  if (avatar) {
    setPortraitUrl(avatar)
  }

  if (urls.id != null) {
    setActiveAvatarId(urls.id)
  }

  return { avatar, seedBack, seedFront }
}

// 应用启动时从后端拉当前头像。由 root.tsx 在用户鉴权通过后触发；
// 404（onboarding 还没头像）是预期情况，让 atom 保持 null 即可。
export async function hydratePortrait(): Promise<void> {
  try {
    const res = await window.spiritagent.api<{
      id?: number
      asset_url?: string
    }>({
      path: '/api/companion/avatar'
    })

    await applyPortrait({
      id: res?.id,
      assetUrl: res?.asset_url
    })
  } catch (error) {
    if (!isClientErrorIpc(error)) {
      log.warn('portrait', 'hydratePortrait failed', error)
    }
  }
}

// 应用启动时从后端拉头像历史。不拉的话每次重启后画廊缩略图都空的——
// 用户只能看到当前 avatar，想看别的样图只能重新生成。
//
// 客户端按追加顺序展示（最早在前），后端按 desc 返回；反转过来
// 让 pushPortraitEntry 能按时间顺序追加。
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

    // 重新填充前先清掉本地残留——否则一次部分 hydrate 会让用户看到的条目
    // 比服务端实际持有的还少。
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

// 用户在按「重新生成」之前输入的反馈文本。在所有暴露重生流程的面板间共享
// （onboarding / 伙伴设置 / 重新对话微调性格 / 角色 inline 编辑），
// 这样用户在某个面板里输入了一半再切到另一个面板时草稿不会丢。
// 每次重生成功后由 useRegeneratePortrait 清掉。
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
