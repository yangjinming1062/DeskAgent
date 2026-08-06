import type { DesktopTheme, DesktopThemeTypography } from './types'

// Color-emoji fonts to append to every stack as a last resort. None of the UI
// text/mono fonts carry emoji glyphs, so without this emoji render as tofu
// boxes on platforms whose default text font lacks them. Covers macOS,
// Windows, plus the `emoji` generic for anything else.
export const EMOJI_FALLBACK = '"Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", "Noto Color Emoji", emoji'

const SYSTEM_SANS =
  '"Segoe WPC", "Segoe UI", -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", system-ui, sans-serif, ' +
  EMOJI_FALLBACK

const SYSTEM_MONO =
  '"Cascadia Code", "JetBrains Mono", "SF Mono", ui-monospace, Menlo, Monaco, Consolas, monospace, ' + EMOJI_FALLBACK

export const DEFAULT_TYPOGRAPHY: DesktopThemeTypography = { fontSans: SYSTEM_SANS, fontMono: SYSTEM_MONO }

const DESKAGENT_BLUE = '#0053FD'
const PSYCHE_BLUE = '#1540B1'
const PSYCHE_WARM = '#FFE6CB'

const deskagentTint = (pct: number) => `color-mix(in srgb, ${DESKAGENT_BLUE} ${pct}%, #FFFFFF)`
const deskagentTintTransparent = (pct: number) => `color-mix(in srgb, ${DESKAGENT_BLUE} ${pct}%, transparent)`

/** DeskAgent — canonical desktop identity. The palette keeps the current
 * glass geometry neutral, then lets the old bb/gui blue and psyche cream
 * return as accent seeds. */
export const deskagentTheme: DesktopTheme = {
  name: 'deskagent',
  label: 'DeskAgent',
  description: 'Glass neutrals with DeskAgent blue accents',
  colors: {
    background: '#F8FAFF',
    foreground: '#17171A',
    card: '#FFFFFF',
    cardForeground: '#17171A',
    muted: deskagentTint(5),
    mutedForeground: '#666678',
    popover: '#FFFFFF',
    popoverForeground: '#17171A',
    primary: DESKAGENT_BLUE,
    primaryForeground: '#FCFCFC',
    secondary: deskagentTint(7),
    secondaryForeground: '#242432',
    accent: deskagentTint(10),
    accentForeground: '#202030',
    border: deskagentTintTransparent(22),
    input: deskagentTintTransparent(30),
    ring: DESKAGENT_BLUE,
    midground: DESKAGENT_BLUE,
    composerRing: DESKAGENT_BLUE,
    destructive: '#C72E4D',
    destructiveForeground: '#FFFFFF',
    sidebarBackground: '#F3F7FF',
    sidebarBorder: deskagentTintTransparent(18),
    userBubble: deskagentTint(6),
    userBubbleBorder: deskagentTintTransparent(24)
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
    midground: DESKAGENT_BLUE,
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
