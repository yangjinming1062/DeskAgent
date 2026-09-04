import { atom } from 'nanostores'

/** 「设置」面板的全部页：八个应用层（原工具窗）+ 五个伙伴层（原 companion panel）。
 *  - `appearance`        —— 外观 / 主题（原工具窗）
 *  - `sprite-appearance` —— 伙伴形象 / 渲染模式 + 缩放（原 companion panel，id 原为 `appearance`）
 *  - `skills`            —— 含子分段：技能 / 工具集
 */
export type SettingsView =
  | 'inference'
  | 'speech'
  | 'channels'
  | 'appearance'
  | 'shortcuts'
  | 'runner'
  | 'skills'
  | 'about'
  | 'persona'
  | 'voice'
  | 'wardrobe'
  | 'sprite-appearance'
  | 'interaction'

export const $settingsView = atom<SettingsView>('persona')

/** 供右键菜单 / 通知等外部入口直达指定设置页。 */
export function setSettingsView(view: SettingsView): void {
  $settingsView.set(view)
}
