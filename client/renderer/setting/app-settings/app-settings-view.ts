import { atom } from 'nanostores'

/** 「设置」面板的 tab：八个应用层（原工具窗）+ 房间管理。 */
export type AppSettingsView =
  | 'inference'
  | 'speech'
  | 'channels'
  | 'appearance'
  | 'room'
  | 'shortcuts'
  | 'runner'
  | 'skills'
  | 'about'

export const $appSettingsView = atom<AppSettingsView>('inference')

export function setAppSettingsView(view: AppSettingsView): void {
  $appSettingsView.set(view)
}
