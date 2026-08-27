import './styles.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'

import { CompanionRoot } from '@/companion'
import { initCompanionPrefsSync } from '@/companion/prefs'
import { ErrorBoundary } from '@/shared/components/error-boundary'
import { HapticsProvider } from '@/shared/components/haptics-provider'
import { installClipboardShim } from '@/shared/lib/clipboard'
import { initUiThemeSync } from '@/shared/store/theme'

installClipboardShim()
initUiThemeSync()
initCompanionPrefsSync()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary label="sprite-root">
      <HapticsProvider>
        <HashRouter>
          <CompanionRoot />
        </HashRouter>
      </HapticsProvider>
    </ErrorBoundary>
  </StrictMode>
)
