import type { Locale } from './types'

// `DEFAULT_LOCALE` is the canonical English baseline — also the runtime fallback
// when an active locale cannot resolve a key (see `runtime.translateNow`).
// `INITIAL_LOCALE` is the first-run user-facing default chosen by product.
// They are deliberately separate: a future "regional language" (e.g. fr) can
// ship without changing the user-facing default.
export const DEFAULT_LOCALE: Locale = 'en'
export const INITIAL_LOCALE: Locale = 'zh'

export const LOCALE_OPTIONS = [
  {
    id: 'en',
    name: 'English',
    englishName: 'English',
    configValue: 'en'
  },
  {
    id: 'zh',
    name: '简体中文',
    englishName: 'Simplified Chinese',
    configValue: 'zh'
  }
] as const satisfies readonly { configValue: string; englishName: string; id: Locale; name: string }[]

// `name` is the endonym (native name) shown in the picker so users recognize
// their language regardless of the current UI language. No country flags:
// languages are not countries. `englishName` is search-only (not shown) so an
// English speaker can type "chinese"/"simplified" to filter the list.
export const LOCALE_META: Record<Locale, { name: string; englishName: string }> = Object.fromEntries(
  LOCALE_OPTIONS.map(locale => [locale.id, { name: locale.name, englishName: locale.englishName }])
) as Record<Locale, { name: string; englishName: string }>

const LOCALE_ALIASES: Record<string, Locale> = {
  en: 'en',
  'en-us': 'en',
  en_us: 'en',
  zh: 'zh',
  'zh-cn': 'zh',
  zh_cn: 'zh'
}

export function isLocale(value: unknown): value is Locale {
  return typeof value === 'string' && LOCALE_OPTIONS.some(locale => locale.id === value)
}

export function normalizeLocale(value: unknown): Locale {
  if (typeof value !== 'string') {
    return DEFAULT_LOCALE
  }

  return LOCALE_ALIASES[value.trim().toLowerCase()] ?? DEFAULT_LOCALE
}

export function isSupportedLocaleValue(value: unknown): boolean {
  return typeof value === 'string' && LOCALE_ALIASES[value.trim().toLowerCase()] != null
}

export function localeConfigValue(locale: Locale): string {
  return LOCALE_OPTIONS.find(item => item.id === locale)?.configValue ?? DEFAULT_LOCALE
}
