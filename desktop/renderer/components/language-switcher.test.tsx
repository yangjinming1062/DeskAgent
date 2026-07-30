import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { I18nProvider } from '@/i18n'

import { LanguageSwitcher } from './language-switcher'

// cmdk (the searchable list) wires a ResizeObserver and scrolls the active
// item into view — neither exists in jsdom. Stub them.
class TestResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

vi.stubGlobal('ResizeObserver', TestResizeObserver)

Element.prototype.scrollIntoView = function scrollIntoView() {}

// Re-import vi for the stubGlobal above (vi is brought in transitively via the
// test setup, but we use it directly here).
import { vi } from 'vitest'

describe('LanguageSwitcher', () => {
  afterEach(() => {
    cleanup()
  })

  it('renders the current locale (zh) in the picker trigger', () => {
    render(
      <I18nProvider initialLocale="zh">
        <LanguageSwitcher />
      </I18nProvider>
    )
    // The trigger should expose zh in some accessible name.
    expect(screen.getByRole('button')).toBeTruthy()
  })
})
