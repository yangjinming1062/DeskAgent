import type { SpiritAgentUiTheme } from '@ipc/contracts'
import { atom } from 'nanostores'

import { persistString, storedString } from '@/shared/lib/storage'
import { normalizeThemeId } from '@/shared/theme/registry'

const THEME_STORAGE_KEY = 'da.ui.theme'

export const $theme = atom<SpiritAgentUiTheme>(normalizeThemeId(storedString(THEME_STORAGE_KEY)))

function applyThemeToDocument(theme: SpiritAgentUiTheme): void {
  document.documentElement.dataset.theme = theme
}

// 模块加载即应用：两个 entry 都先 import styles.css 再 import 本模块，
// 首帧渲染前 data-theme 已就位，无错误主题闪烁。
applyThemeToDocument($theme.get())

export function setUiTheme(theme: SpiritAgentUiTheme): void {
  persistString(THEME_STORAGE_KEY, theme)
  $theme.set(theme)
  applyThemeToDocument(theme)
  // 主进程广播到两个窗口（含本窗，回声幂等）；持久化只由发起切换的窗口写。
  window.spiritagent?.setUiTheme?.(theme)
}

// 订阅主进程主题广播（另一窗口切换时同步本窗口）；两个 entry 的模块作用域各调用一次。
export function initUiThemeSync(): () => void {
  const unsubscribe = window.spiritagent?.onUiThemeChanged?.(payload => {
    const theme = normalizeThemeId(payload?.theme)

    persistString(THEME_STORAGE_KEY, theme)
    $theme.set(theme)
    applyThemeToDocument(theme)
  })

  return unsubscribe ?? (() => {})
}
