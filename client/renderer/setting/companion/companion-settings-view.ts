import { atom } from 'nanostores'

/** 「伙伴设置」面板的 tab：角色与记忆 / 音色 / 交互。 */
export type CompanionSettingsView = 'persona' | 'voice' | 'interaction'

export const $companionSettingsView = atom<CompanionSettingsView>('persona')

/** 供通知 deep-link 等外部入口直达指定 tab。 */
export function setCompanionSettingsView(view: CompanionSettingsView): void {
  $companionSettingsView.set(view)
}
