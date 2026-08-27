import type { SpiritAgentUiTheme } from '@ipc/contracts'

// 主题元数据注册表——UI 色值的权威在 styles.css 的 html[data-theme] 变量块；
// 这里只存 CSS 存不了的：展示名与预览色板。
// 新增主题三步：styles.css 加变量块 + 特效规则 → SPIRITAGENT_UI_THEMES 扩枚举 → 这里加一条。
export interface ThemeDefinition {
  id: SpiritAgentUiTheme
  label: string
  description: string
  preview: { chrome: string; panel: string; card: string; accent: string }
}

export const DEFAULT_THEME_ID: SpiritAgentUiTheme = 'classic'

export const THEMES: readonly ThemeDefinition[] = [
  {
    id: 'classic',
    label: '经典暗色',
    description: '石墨分层暗色，清晰稳重。',
    preview: { chrome: '#0f0f11', panel: '#141416', card: '#1c1c21', accent: '#6c8aff' }
  },
  {
    id: 'cyber-glass',
    label: '赛博玻璃',
    description: '玻璃拟态半透面板，蓝紫渐变、像素噪点与发光描边。',
    preview: { chrome: '#0a0c18', panel: '#101324', card: '#181c34', accent: '#7d9bff' }
  },
  {
    id: 'holo',
    label: '全息 HUD',
    description: '青色细线全息投影，扫描线、角标括弧与呼吸光效。',
    preview: { chrome: '#060d12', panel: '#081218', card: '#0b1a21', accent: '#4de8ff' }
  }
]

export function normalizeThemeId(raw: null | string): SpiritAgentUiTheme {
  const found = THEMES.find(t => t.id === raw)

  return found ? found.id : DEFAULT_THEME_ID
}
