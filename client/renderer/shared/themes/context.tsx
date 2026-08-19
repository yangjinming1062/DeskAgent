import { DEFAULT_TYPOGRAPHY, spiritagentTheme } from './presets'
import type { DesktopTheme, DesktopThemeColors } from './types'

export type ThemeMode = 'light' | 'dark' | 'system'

// ─── 颜色计算（用于合成浅色变体） ────────────────────────

function hexToRgb(hex: string): [number, number, number] | null {
  const clean = hex.trim().replace(/^#/, '')

  if (!/^[0-9a-f]{6}$/i.test(clean)) {
    return null
  }

  return [0, 2, 4].map(i => parseInt(clean.slice(i, i + 2), 16)) as [number, number, number]
}

const rgbToHex = ([r, g, b]: [number, number, number]) =>
  `#${[r, g, b].map(n => Math.round(n).toString(16).padStart(2, '0')).join('')}`

function mix(a: string, b: string, amount: number): string {
  const ar = hexToRgb(a)
  const br = hexToRgb(b)

  return ar && br
    ? rgbToHex([ar[0] + (br[0] - ar[0]) * amount, ar[1] + (br[1] - ar[1]) * amount, ar[2] + (br[2] - ar[2]) * amount])
    : a
}

function readableOn(hex: string): string {
  const rgb = hexToRgb(hex)

  if (!rgb) {
    return '#ffffff'
  }

  const [r, g, b] = rgb.map(v => {
    const c = v / 255

    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  })

  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.58 ? '#161616' : '#ffffff'
}

function synthLightColors(seed: DesktopTheme): DesktopThemeColors {
  const accent = seed.colors.ring || seed.colors.primary
  const soft = mix('#ffffff', accent, 0.1)
  const softer = mix('#ffffff', accent, 0.06)
  const border = mix('#ececef', accent, 0.14)
  const midground = seed.colors.midground ?? accent

  return {
    background: '#ffffff',
    foreground: '#161616',
    card: '#ffffff',
    cardForeground: '#161616',
    muted: softer,
    mutedForeground: mix('#6b6b70', accent, 0.16),
    popover: '#ffffff',
    popoverForeground: '#161616',
    primary: accent,
    primaryForeground: readableOn(accent),
    secondary: soft,
    secondaryForeground: mix('#2a2a2a', accent, 0.34),
    accent: soft,
    accentForeground: mix('#2a2a2a', accent, 0.34),
    border,
    input: mix('#e2e2e6', accent, 0.18),
    ring: accent,
    midground,
    midgroundForeground: readableOn(midground),
    destructive: '#b94a3a',
    destructiveForeground: '#ffffff',
    sidebarBackground: mix('#fafafa', accent, 0.05),
    sidebarBorder: border,
    userBubble: soft,
    userBubbleBorder: border
  }
}

function getBaseColors(mode: 'light' | 'dark'): DesktopThemeColors {
  if (mode === 'dark') {
    return spiritagentTheme.darkColors ?? spiritagentTheme.colors
  }

  return spiritagentTheme.darkColors ? spiritagentTheme.colors : synthLightColors(spiritagentTheme)
}

function deriveTheme(mode: 'light' | 'dark'): DesktopTheme {
  return {
    ...spiritagentTheme,
    name: `spiritagent-${mode}`,
    label: `${spiritagentTheme.label} ${mode === 'light' ? 'Light' : 'Dark'}`,
    description: `${spiritagentTheme.label} ${mode} palette`,
    colors: getBaseColors(mode)
  }
}

function renderedModeFor(colors: DesktopThemeColors, mode: 'light' | 'dark'): 'light' | 'dark' {
  const rgb = hexToRgb(colors.background)

  if (!rgb) {
    return mode
  }

  const [r, g, b] = rgb.map(v => v / 255)

  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.5 ? 'light' : 'dark'
}

// ─── CSS 应用 ────────────────────────────────────────────────────────

const mixesFor = (isDark: boolean): Record<string, string> => ({
  '--theme-mix-chrome': isDark ? '74%' : '92%',
  '--theme-mix-sidebar': '100%',
  '--theme-mix-card': isDark ? '38%' : '22%',
  '--theme-mix-elevated': isDark ? '46%' : '28%',
  '--theme-mix-bubble': isDark ? '46%' : '0%'
})

function applyTheme(theme: DesktopTheme, mode: 'light' | 'dark'): void {
  if (typeof document === 'undefined') {
    return
  }

  const root = document.documentElement
  const c = theme.colors

  // B4：spiritagentTheme.typography 同时覆盖 fontSans 和 fontMono，
  // 所以 DEFAULT_TYPOGRAPHY 回退在我们出厂的主题里实际上是死代码。
  // 保留 `theme.typography` 的展开是为了让消费方按主题覆写。
  // 我们用 `DEFAULT_TYPOGRAPHY` 做兜底，是因为 `DesktopTheme.typography` 是
  // `Partial<...>`，即便 spiritagentTheme 自己的值类型也是 `string | undefined`。
  // 默认值与我们之前展开的值一致。
  const typo = {
    fontSans: theme.typography?.fontSans ?? spiritagentTheme.typography?.fontSans ?? DEFAULT_TYPOGRAPHY.fontSans,
    fontMono: theme.typography?.fontMono ?? spiritagentTheme.typography?.fontMono ?? DEFAULT_TYPOGRAPHY.fontMono,
    fontUrl: theme.typography?.fontUrl ?? spiritagentTheme.typography?.fontUrl
  }

  const rendered = renderedModeFor(c, mode)
  const isDark = rendered === 'dark'
  const midground = c.midground ?? c.ring

  root.style.setProperty('color-scheme', rendered)
  root.dataset.spiritagentMode = rendered
  root.classList.toggle('dark', isDark)

  // 品牌种子通过 styles.css 中的 `color-mix()` 喂给所有 glass + shadcn token。
  const seeds: Record<string, string> = {
    '--theme-foreground': c.foreground,
    '--theme-primary': c.primary,
    '--theme-secondary': c.secondary,
    '--theme-accent-soft': c.accent,
    '--theme-midground': midground,
    '--theme-warm': c.primary,
    '--theme-background-seed': c.background,
    '--theme-sidebar-seed': c.sidebarBackground ?? c.background,
    '--theme-card-seed': c.card,
    '--theme-elevated-seed': c.popover,
    '--theme-bubble-seed': c.userBubble ?? c.popover
  }

  // 不由种子链派生的 shadcn / Tailwind token。
  const palette: Record<string, string> = {
    '--dt-primary-foreground': c.primaryForeground,
    '--dt-secondary-foreground': c.secondaryForeground,
    '--dt-accent-foreground': c.accentForeground,
    '--dt-border': c.border,
    '--dt-input': c.input,
    '--dt-ring': c.ring,
    '--dt-muted': c.muted,
    '--dt-midground-foreground': c.midgroundForeground ?? readableOn(midground),
    '--dt-composer-ring': c.composerRing ?? midground,
    '--dt-destructive': c.destructive,
    '--dt-destructive-foreground': c.destructiveForeground,
    '--dt-sidebar-border': c.sidebarBorder ?? c.border,
    '--dt-user-bubble-border': c.userBubbleBorder ?? c.border,
    '--dt-font-sans': typo.fontSans,
    '--dt-font-mono': typo.fontMono,
    '--noise-opacity-mul': isDark ? 'calc(0.04 / 0.21)' : 'calc(0.34 / 0.21)'
  }

  for (const [k, v] of Object.entries({ ...seeds, ...mixesFor(isDark), ...palette })) {
    root.style.setProperty(k, v)
  }

  window.spiritagent?.setTitleBarTheme?.({
    background: c.background,
    foreground: c.foreground
  })

  // B6：在此注入主题样式表。整套只有一份字体 URL
  // （Courier Prime，来自 spiritagentTheme.typography.fontUrl），
  // 而 applyTheme 如今只在下方模块加载的启动块中被调用。
  // 之前那个 Set + dataset 守卫对单个 URL 是无用的开销。
  if (typo.fontUrl && !document.head.querySelector(`link[data-spiritagent-theme-font]`)) {
    const link = document.createElement('link')

    link.rel = 'stylesheet'
    link.href = typo.fontUrl
    link.dataset.spiritagentThemeFont = 'true'
    document.head.appendChild(link)
  }
}

// 启动时绘制：在初始渲染时一次性 import，同步应用，
// 让页面不会出现默认主题闪烁。带框架的工具窗口例外——
// styles.css 在 html[data-role='tool'] 上钉死了深色调色板，
// 此处内联种子反而会赢过那一级 cascade。
if (typeof window !== 'undefined' && document.documentElement.dataset.role !== 'tool') {
  applyTheme(deriveTheme('light'), 'light')
}
