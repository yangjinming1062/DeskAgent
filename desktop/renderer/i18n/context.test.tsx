import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { I18nProvider, useI18n } from './context'
import type { Locale } from './types'

const LOCALE_STORAGE_KEY = 'deskagent.desktop.locale.v1'

function LanguageProbe({ target = 'zh' }: { target?: Locale }) {
  const { locale, saveError, setLocale, t } = useI18n()

  return (
    <div>
      <p data-testid="locale">{locale}</p>
      <p data-testid="label">{t.language.label}</p>
      <p data-testid="save">{t.common.save}</p>
      <p data-testid="save-error">{saveError?.message ?? ''}</p>
      <button onClick={() => void setLocale(target).catch(() => undefined)} type="button">
        switch
      </button>
    </div>
  )
}

describe('I18nProvider', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    cleanup()
    window.localStorage.clear()
  })

  it('defaults to zh when no initial locale or stored preference is supplied', () => {
    render(
      <I18nProvider>
        <LanguageProbe />
      </I18nProvider>
    )

    expect(screen.getByTestId('locale').textContent).toBe('zh')
    expect(screen.getByTestId('label').textContent).toBe('语言')
  })

  it('normalizes an initial locale alias', () => {
    render(
      <I18nProvider initialLocale="zh-CN">
        <LanguageProbe />
      </I18nProvider>
    )

    expect(screen.getByTestId('locale').textContent).toBe('zh')
    expect(screen.getByTestId('label').textContent).toBe('语言')
  })

  it('rehydrates the locale from localStorage when no initial locale is supplied', () => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, 'en')

    render(
      <I18nProvider>
        <LanguageProbe />
      </I18nProvider>
    )

    expect(screen.getByTestId('locale').textContent).toBe('en')
    expect(screen.getByTestId('save').textContent).toBe('Save')
  })

  it('initialLocale overrides the persisted preference', () => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, 'en')

    render(
      <I18nProvider initialLocale="zh">
        <LanguageProbe />
      </I18nProvider>
    )

    expect(screen.getByTestId('locale').textContent).toBe('zh')
  })

  it('setLocale switches the active locale and persists to localStorage', async () => {
    render(
      <I18nProvider initialLocale="zh">
        <LanguageProbe target="en" />
      </I18nProvider>
    )

    expect(screen.getByTestId('locale').textContent).toBe('zh')
    fireEvent.click(screen.getByRole('button', { name: 'switch' }))

    await waitFor(() => expect(screen.getByTestId('locale').textContent).toBe('en'))
    expect(screen.getByTestId('save').textContent).toBe('Save')
    expect(screen.getByTestId('save-error').textContent).toBe('')
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('en')
  })

  it('setLocale switches back to zh and updates persisted preference', async () => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, 'en')

    render(
      <I18nProvider>
        <LanguageProbe target="zh" />
      </I18nProvider>
    )

    expect(screen.getByTestId('locale').textContent).toBe('en')
    fireEvent.click(screen.getByRole('button', { name: 'switch' }))

    await waitFor(() => expect(screen.getByTestId('locale').textContent).toBe('zh'))
    expect(screen.getByTestId('save').textContent).toBe('保存')
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('zh')
  })
})
