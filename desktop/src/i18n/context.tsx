import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from 'react'

import { persistString, storedString } from '@/lib/storage'

import { TRANSLATIONS } from './catalog'
import { DEFAULT_LOCALE, INITIAL_LOCALE, localeConfigValue, normalizeLocale } from './languages'
import { setRuntimeI18nLocale } from './runtime'
import type { Locale, Translations } from './types'

export { LOCALE_META } from './languages'

export function withConfigDisplayLanguage(config: Record<string, unknown>, locale: Locale): Record<string, unknown> {
  const display =
    typeof config.display === 'object' && config.display !== null && !Array.isArray(config.display)
      ? (config.display as Record<string, unknown>)
      : {}

  return {
    ...config,
    display: {
      ...display,
      language: localeConfigValue(locale)
    }
  }
}

export interface I18nContextValue {
  isSavingLocale: boolean
  locale: Locale
  saveError: Error | null
  setLocale: (next: Locale) => Promise<void>
  t: Translations
}

const I18nContext = createContext<I18nContextValue>({
  isSavingLocale: false,
  locale: DEFAULT_LOCALE,
  saveError: null,
  setLocale: async () => {},
  t: TRANSLATIONS[DEFAULT_LOCALE]
})

// localStorage key for the persisted renderer locale preference.
// Naming follows the `zast.desktop.<scope>.v1` convention (see e.g.
// `zast.desktop.sessionPreviews.v1`). Bump the suffix if the persisted shape
// changes in a non-backwards-compatible way.
const LOCALE_STORAGE_KEY = 'zast.desktop.locale.v1'

export interface I18nProviderProps {
  children: ReactNode
  initialLocale?: unknown
}

export function I18nProvider({ children, initialLocale }: I18nProviderProps) {
  // Resolution priority: explicit `initialLocale` (tests / future server-driven
  // seed) > persisted user preference > `INITIAL_LOCALE`. Keeping `initialLocale`
  // first lets tests inject a known locale regardless of localStorage state.
  const [locale, setLocaleState] = useState<Locale>(() =>
    normalizeLocale(initialLocale ?? storedString(LOCALE_STORAGE_KEY) ?? INITIAL_LOCALE)
  )

  const [saveError, setSaveError] = useState<Error | null>(null)
  const [isSavingLocale, setIsSavingLocale] = useState(false)

  useEffect(() => {
    setRuntimeI18nLocale(locale)
  }, [locale])

  const setLocale = useCallback(async (next: Locale) => {
    setIsSavingLocale(true)

    try {
      persistString(LOCALE_STORAGE_KEY, next)
      setLocaleState(normalizeLocale(next))
      setSaveError(null)
    } catch (error) {
      setSaveError(error instanceof Error ? error : new Error(String(error)))
      throw error
    } finally {
      setIsSavingLocale(false)
    }
  }, [])

  const value = useMemo<I18nContextValue>(
    () => ({
      isSavingLocale,
      locale,
      saveError,
      setLocale,
      t: TRANSLATIONS[locale]
    }),
    [isSavingLocale, locale, saveError, setLocale]
  )

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

export function useI18n(): I18nContextValue {
  return useContext(I18nContext)
}
