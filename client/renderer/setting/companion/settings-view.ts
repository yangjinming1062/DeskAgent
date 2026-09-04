import { atom } from 'nanostores'

export type SettingsView = 'persona' | 'voice' | 'wardrobe' | 'appearance' | 'interaction'

export const $settingsView = atom<SettingsView>('persona')

/** 供右键菜单 / 通知等外部入口直达指定设置页。 */
export function setSettingsView(view: SettingsView): void {
  $settingsView.set(view)
}
