// 生活空间右栏当前选中页：聊天 / 衣橱 / 形象 / 片刻 / 日记 / 通道 / 房间 / 设置。
//
// 视图通过 hash 路由持久化（hash 变化也持久化到 localStorage），
// 这样工作台切到生活空间时能恢复到上次的视图。

import { atom } from 'nanostores'

import type { AppSettingsView } from '@/setting/app-settings/app-settings-view'
import { $appSettingsView } from '@/setting/app-settings/app-settings-view'

export type LivingView = 'chat' | 'wardrobe' | 'appearance' | 'moments' | 'diary' | 'channels' | 'room' | 'settings'

export const LIVING_VIEWS: ReadonlyArray<LivingView> = [
  'chat',
  'wardrobe',
  'appearance',
  'moments',
  'diary',
  'channels',
  'room',
  'settings'
]

const STORAGE_KEY = 'da.living.view'
const DEFAULT_VIEW: LivingView = 'chat'

const LIVING_TO_APP_SETTINGS: Partial<Record<LivingView, AppSettingsView>> = {
  appearance: 'appearance',
  channels: 'channels'
}

function parseHashView(): LivingView | null {
  if (typeof window === 'undefined' || !window.location.hash) {
    return null
  }

  const clean = window.location.hash.replace(/^#\/?/, '').trim() as LivingView

  return LIVING_VIEWS.includes(clean) ? clean : null
}

function readStored(): LivingView {
  const fromHash = parseHashView()

  if (fromHash) {
    return fromHash
  }

  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    const valid = LIVING_VIEWS.includes(raw as LivingView)

    return valid ? (raw as LivingView) : DEFAULT_VIEW
  } catch {
    return DEFAULT_VIEW
  }
}

function writeStored(view: LivingView): void {
  try {
    localStorage.setItem(STORAGE_KEY, view)
  } catch {
    /* ignore quota / disabled storage */
  }
}

export const $livingView = atom<LivingView>(readStored())

export function setLivingView(view: LivingView): void {
  writeStored(view)
  $livingView.set(view)

  // 同步 hash，方便从外链 deep-link
  if (typeof window !== 'undefined' && window.location.hash !== `#/${view}`) {
    window.history.replaceState(null, '', `#/${view}`)
  }

  const sub = LIVING_TO_APP_SETTINGS[view]

  if (sub && $appSettingsView.get() !== sub) {
    $appSettingsView.set(sub)
  }
}

if (typeof window !== 'undefined') {
  window.addEventListener('hashchange', () => {
    const fromHash = parseHashView()

    if (fromHash && $livingView.get() !== fromHash) {
      setLivingView(fromHash)
    }
  })
}
