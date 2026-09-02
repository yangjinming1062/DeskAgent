import './styles.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'

import { ToolRoot } from '@/hub'
import { ErrorBoundary } from '@/shared/components/error-boundary'
import { HapticsProvider } from '@/shared/components/haptics-provider'
import { installClipboardShim } from '@/shared/lib/clipboard'
import { installUpdateBridge } from '@/shared/lib/update-bridge'
import { initUiThemeSync } from '@/shared/store/theme'

installClipboardShim()
initUiThemeSync()
const offUpdateBridge = installUpdateBridge()

if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    offUpdateBridge()
  })
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary label="hub-root">
      <HapticsProvider>
        <HashRouter>
          <ToolRoot />
        </HashRouter>
      </HapticsProvider>
    </ErrorBoundary>
  </StrictMode>
)
