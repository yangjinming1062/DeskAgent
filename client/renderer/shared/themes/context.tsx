import { type ReactNode } from 'react'

import { DEFAULT_TYPOGRAPHY, deskagentTheme } from './presets'
import type { DesktopTheme, DesktopThemeColors } from './types'

export type ThemeMode = 'light' | 'dark' | 'system'

// ─── Color math (for synthesised light variants) ────────────────────────

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
    return deskagentTheme.darkColors ?? deskagentTheme.colors
  }

  return deskagentTheme.darkColors ? deskagentTheme.colors : synthLightColors(deskagentTheme)
}

function deriveTheme(mode: 'light' | 'dark'): DesktopTheme {
  return {
    ...deskagentTheme,
    name: `deskagent-${mode}`,
    label: `${deskagentTheme.label} ${mode === 'light' ? 'Light' : 'Dark'}`,
    description: `${deskagentTheme.label} ${mode} palette`,
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

// ─── CSS application ────────────────────────────────────────────────────────

const mixesFor = (isDark: boolean): Record<string, string> => ({
  '--theme-mix-chrome': isDark ? '74%' : '92%',
  '--theme-mix-sidebar': '100%',
  '--theme-mix-card': isDark ? '38%' : '22%',
  '--theme-mix-elevated': isDark ? '46%' : '28%',
  '--theme-mix-bubble': isDark ? '46%' : '0%'
})

function applyTheme(theme: DesktopTheme, mode: 'light' | 'dark') {
  if (typeof document === 'undefined') {
    return
  }

  const root = document.documentElement
  const c = theme.colors

  // B4: deskagentTheme.typography covers both fontSans and fontMono, so the
  // DEFAULT_TYPOGRAPHY fallback is dead in practice for our shipped themes.
  // The remaining `theme.typography` spread still lets consumers override
  // per-theme. We coalesce against `DEFAULT_TYPOGRAPHY` because
  // `DesktopTheme.typography` is `Partial<...>` so even deskagentTheme's
  // values are typed `string | undefined`. The default matches what we
  // used to spread in.
  const typo = {
    fontSans: theme.typography?.fontSans ?? deskagentTheme.typography?.fontSans ?? DEFAULT_TYPOGRAPHY.fontSans,
    fontMono: theme.typography?.fontMono ?? deskagentTheme.typography?.fontMono ?? DEFAULT_TYPOGRAPHY.fontMono,
    fontUrl: theme.typography?.fontUrl ?? deskagentTheme.typography?.fontUrl
  }

  const rendered = renderedModeFor(c, mode)
  const isDark = rendered === 'dark'
  const midground = c.midground ?? c.ring

  root.style.setProperty('color-scheme', rendered)
  root.dataset.deskagentMode = rendered
  root.classList.toggle('dark', isDark)

  // Brand seeds feed every glass + shadcn token via `color-mix()` in styles.css.
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

  // shadcn/Tailwind tokens that aren't derived from the seed chain.
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

  window.deskagent?.setTitleBarTheme?.({
    background: c.background,
    foreground: c.foreground
  })

  // B6: inject the theme stylesheet here. There's exactly one font URL
  // (Courier Prime from deskagentTheme.typography.fontUrl), and
  // applyTheme is now only called from the module-load boot block below.
  // The previous Set + dataset guard was dead overhead for a single URL.
  if (typo.fontUrl && !document.head.querySelector(`link[data-deskagent-theme-font]`)) {
    const link = document.createElement('link')

    link.rel = 'stylesheet'
    link.href = typo.fontUrl
    link.dataset.deskagentThemeFont = 'true'
    document.head.appendChild(link)
  }
}

// Boot-time paint to avoid a flash before <ThemeProvider> mounts. The
// module-load call alone covers initial paint; B3 dropped the redundant
// useEffect in <ThemeProvider> because it duplicated this work and would
// race against the boot block on HMR remount.
if (typeof window !== 'undefined') {
  applyTheme(deriveTheme('light'), 'light')
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  return <>{children}</>
}
