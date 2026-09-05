import { normalizeUiTheme, type SpiritAgentUiTheme } from '@ipc/contracts'

// 主题元数据注册表——UI 色值的权威在 styles.css 的 html[data-theme] 变量块；
// 这里只存 CSS 存不了的：展示名与预览色板。
// 「动态」主题共享夜色的 CSS 变量定义，只在运行时由 companion/persona-skin.ts
// 覆写强调色（--ui-accent* / --persona-*），底色 token 始终是夜色。
export interface ThemeDefinition {
  id: SpiritAgentUiTheme
  label: string
  description: string
  preview: { chrome: string; panel: string; card: string; accent: string }
}

export const THEMES: readonly ThemeDefinition[] = [
  {
    id: 'dynamic',
    label: '动态',
    description: '强调色随当前角色立绘自动抽取，日夜底色取自夜色；切换角色或换装后 1.5s 内过渡换皮。',
    preview: { chrome: '#101014', panel: '#141418', card: '#1c1c22', accent: '#8aa0c8' }
  },
  {
    id: 'night',
    label: '夜色',
    description: '液态毛玻璃半透面板，0.6px 白描边与低饱和蓝灰强调色。',
    preview: { chrome: '#101014', panel: '#141418', card: '#1c1c22', accent: '#8aa0c8' }
  },
  {
    id: 'day',
    label: '日色',
    description: '通透高质感浅色玻璃面板，深石墨文字与优雅沉静质感。',
    preview: { chrome: '#f6f4f0', panel: '#fcfaf8', card: '#ffffff', accent: '#5c7094' }
  }
]

export function normalizeThemeId(raw: null | string | undefined): SpiritAgentUiTheme {
  return normalizeUiTheme(raw)
}
