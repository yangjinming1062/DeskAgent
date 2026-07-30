import { describe, expect, it } from 'vitest'

import {
  DEFAULT_LOCALE,
  INITIAL_LOCALE,
  isLocale,
  isSupportedLocaleValue,
  localeConfigValue,
  normalizeLocale
} from './languages'

describe('desktop i18n languages', () => {
  it('normalizes supported locale aliases', () => {
    expect(normalizeLocale('en')).toBe('en')
    expect(normalizeLocale('EN-US')).toBe('en')
    expect(normalizeLocale('zh')).toBe('zh')
    expect(normalizeLocale('zh-CN')).toBe('zh')
    expect(normalizeLocale(' zh_cn ')).toBe('zh')
  })

  it('falls back to English for empty or unsupported values', () => {
    expect(normalizeLocale(null)).toBe(DEFAULT_LOCALE)
    expect(normalizeLocale('')).toBe(DEFAULT_LOCALE)
    expect(normalizeLocale('de')).toBe(DEFAULT_LOCALE)
    expect(normalizeLocale('zh-TW')).toBe(DEFAULT_LOCALE)
    expect(normalizeLocale('ja')).toBe(DEFAULT_LOCALE)
  })

  it('distinguishes exact locale ids from supported config aliases', () => {
    expect(isSupportedLocaleValue('zh-CN')).toBe(true)
    expect(isSupportedLocaleValue('de')).toBe(false)
    expect(isSupportedLocaleValue('zh-TW')).toBe(false)
    expect(isSupportedLocaleValue('ja-JP')).toBe(false)
    expect(isLocale('zh-CN')).toBe(false)
    expect(isLocale('zh')).toBe(true)
    expect(isLocale('en')).toBe(true)
    expect(isLocale('zh-hant')).toBe(false)
    expect(isLocale('ja')).toBe(false)
  })

  it('returns the persisted config value for supported locales', () => {
    expect(localeConfigValue('en')).toBe('en')
    expect(localeConfigValue('zh')).toBe('zh')
  })

  it('exposes DEFAULT_LOCALE and INITIAL_LOCALE as distinct constants', () => {
    expect(DEFAULT_LOCALE).toBe('en')
    expect(INITIAL_LOCALE).toBe('zh')
  })
})
