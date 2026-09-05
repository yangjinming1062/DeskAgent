// 生活空间右栏当前选中页：聊天 / 衣橱 / 形象 / 片刻 / 日记 / 通道 / 房间 / 设置。
//
// 视图通过 hash 路由持久化（hash 变化也持久化到 localStorage），
// 这样工作台切到生活空间时能恢复到上次的视图。

import { atom } from 'nanostores'

import { definePersistedEnum } from '@/shared/lib/storage'

export type LivingView = 'chat' | 'wardrobe' | 'appearance' | 'moments' | 'diary' | 'channels' | 'room' | 'settings'

export type LivingSettingsSection = 'persona' | 'voice' | 'interaction' | 'theme' | 'speech' | 'shortcuts' | 'about'

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

export const LIVING_SETTINGS_SECTIONS: ReadonlyArray<LivingSettingsSection> = [
  'persona',
  'voice',
  'interaction',
  'theme',
  'speech',
  'shortcuts',
  'about'
]

const SETTINGS_SECTION_PREFIX = 'settings/'

function parseLivingViewFromHash(): LivingView | null {
  if (typeof window === 'undefined' || !window.location.hash) {
    return null
  }

  const cleanPath = window.location.hash.replace(/^#\/?/, '').split('?')[0].trim()
  const topSegment = cleanPath.split('/')[0]

  return (LIVING_VIEWS as ReadonlyArray<string>).includes(topSegment) ? (topSegment as LivingView) : null
}

function parseLivingSettingsSectionFromHash(): LivingSettingsSection | null {
  if (typeof window === 'undefined' || !window.location.hash) {
    return null
  }

  const cleanPath = window.location.hash.replace(/^#\/?/, '').split('?')[0].trim()

  if (!cleanPath.startsWith(SETTINGS_SECTION_PREFIX)) {
    return null
  }

  const section = cleanPath.slice(SETTINGS_SECTION_PREFIX.length).split('/')[0]

  return (LIVING_SETTINGS_SECTIONS as ReadonlyArray<string>).includes(section)
    ? (section as LivingSettingsSection)
    : null
}

function replaceHash(next: string): void {
  if (typeof window !== 'undefined' && window.location.hash !== next) {
    window.history.replaceState(null, '', next)
  }
}

const viewStore = definePersistedEnum<LivingView>({
  allowed: LIVING_VIEWS,
  fallback: 'chat',
  key: 'da.living.view'
})

// settings 视图用 #/settings/<section> 形式深链；hash 解析时去掉 settings/ 前缀。
const sectionStore = definePersistedEnum<LivingSettingsSection>({
  allowed: LIVING_SETTINGS_SECTIONS,
  fallback: 'persona',
  key: 'da.living.settings.section'
})

export const $livingView = atom<LivingView>(parseLivingViewFromHash() ?? viewStore.get())
export const $livingSettingsSection = atom<LivingSettingsSection>(
  parseLivingSettingsSectionFromHash() ?? sectionStore.get()
)

export function setLivingView(view: LivingView): void {
  viewStore.set(view)
  $livingView.set(view)

  if (view === 'settings') {
    replaceHash(`#/settings/${$livingSettingsSection.get()}`)
  } else {
    replaceHash(`#/${view}`)
  }
}

export function setLivingSettingsSection(section: LivingSettingsSection): void {
  viewStore.set('settings')
  $livingView.set('settings')
  sectionStore.set(section)
  $livingSettingsSection.set(section)
  replaceHash(`#/settings/${section}`)
}

if (typeof window !== 'undefined') {
  window.addEventListener('hashchange', () => {
    const view = parseLivingViewFromHash()

    if (view && $livingView.get() !== view) {
      viewStore.set(view)
      $livingView.set(view)
    }

    const section = parseLivingSettingsSectionFromHash()

    if (section && $livingSettingsSection.get() !== section) {
      sectionStore.set(section)
      $livingSettingsSection.set(section)
    }
  })
}
