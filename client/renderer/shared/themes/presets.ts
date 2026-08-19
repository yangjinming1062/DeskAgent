import type { DesktopTheme, DesktopThemeTypography } from './types'

// 兜底追加到每个字体栈末尾的彩色 emoji 字体。所有 UI 正文 / 等宽字体
// 都不携带 emoji 字形，没有这一段，在默认文本字体不含 emoji 的平台上
// 会渲染为豆腐块。覆盖 macOS、Windows，并附带 `emoji` 泛型以应对其他平台。
export const EMOJI_FALLBACK = '"Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", "Noto Color Emoji", emoji'

const SYSTEM_SANS =
  '"Segoe WPC", "Segoe UI", -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", system-ui, sans-serif, ' +
  EMOJI_FALLBACK

const SYSTEM_MONO =
  '"Cascadia Code", "JetBrains Mono", "SF Mono", ui-monospace, Menlo, Monaco, Consolas, monospace, ' + EMOJI_FALLBACK

export const DEFAULT_TYPOGRAPHY: DesktopThemeTypography = { fontSans: SYSTEM_SANS, fontMono: SYSTEM_MONO }

const SPIRITAGENT_BLUE = '#0053FD'
const PSYCHE_BLUE = '#1540B1'
const PSYCHE_WARM = '#FFE6CB'

// B5：把两个几乎相同的 tint 合并成一份。透明变体仅在第二个 `color-mix`
// 参数上有区别——把这个参数传进来即可，不必同时持有同一模板的两份拷贝。
const spiritagentTint = (pct: number, base: '#FFFFFF' | 'transparent' = '#FFFFFF') =>
  `color-mix(in srgb, ${SPIRITAGENT_BLUE} ${pct}%, ${base})`

/** SpiritAgent —— 桌面端的官方身份。调色板让当前的玻璃质感保持中性，
 * 再让旧的 bb/gui 蓝与 psyche 米色作为强调色种子回归。 */
export const spiritagentTheme: DesktopTheme = {
  name: 'spiritagent',
  label: 'SpiritAgent',
  description: 'Glass neutrals with SpiritAgent blue accents',
  colors: {
    background: '#F8FAFF',
    foreground: '#17171A',
    card: '#FFFFFF',
    cardForeground: '#17171A',
    muted: spiritagentTint(5),
    mutedForeground: '#666678',
    popover: '#FFFFFF',
    popoverForeground: '#17171A',
    primary: SPIRITAGENT_BLUE,
    primaryForeground: '#FCFCFC',
    secondary: spiritagentTint(7),
    secondaryForeground: '#242432',
    accent: spiritagentTint(10),
    accentForeground: '#202030',
    border: spiritagentTint(22, 'transparent'),
    input: spiritagentTint(30, 'transparent'),
    ring: SPIRITAGENT_BLUE,
    midground: SPIRITAGENT_BLUE,
    composerRing: SPIRITAGENT_BLUE,
    destructive: '#C72E4D',
    destructiveForeground: '#FFFFFF',
    sidebarBackground: '#F3F7FF',
    sidebarBorder: spiritagentTint(18, 'transparent'),
    userBubble: spiritagentTint(6),
    userBubbleBorder: spiritagentTint(24, 'transparent')
  },
  darkColors: {
    background: '#0D2F86',
    foreground: PSYCHE_WARM,
    card: '#12378F',
    cardForeground: PSYCHE_WARM,
    muted: '#183F9A',
    mutedForeground: '#B5C7F3',
    popover: '#123A96',
    popoverForeground: PSYCHE_WARM,
    primary: PSYCHE_WARM,
    primaryForeground: '#0D2F86',
    secondary: '#1B45A4',
    secondaryForeground: '#E0E8FF',
    accent: PSYCHE_BLUE,
    accentForeground: '#F0F4FF',
    border: '#3158AD',
    input: '#0B2566',
    ring: PSYCHE_WARM,
    midground: SPIRITAGENT_BLUE,
    composerRing: PSYCHE_WARM,
    destructive: '#C0473A',
    destructiveForeground: '#FEF2F2',
    sidebarBackground: '#09286F',
    sidebarBorder: '#234A9C',
    userBubble: '#143B91',
    userBubbleBorder: '#3A63BD'
  },
  typography: {
    fontSans: SYSTEM_SANS,
    fontMono: `"Courier Prime", ${SYSTEM_MONO}`,
    fontUrl: 'https://fonts.googleapis.com/css2?family=Courier+Prime:wght@400;700&display=swap'
  }
}
