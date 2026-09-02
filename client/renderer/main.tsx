import './styles.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'

import { ErrorBoundary } from '@/shared/components/error-boundary'
import { HapticsProvider } from '@/shared/components/haptics-provider'
import { installClipboardShim } from '@/shared/lib/clipboard'
import { installUpdateBridge } from '@/shared/lib/update-bridge'

import App from './app'

installClipboardShim()
const offUpdateBridge = installUpdateBridge()

if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    offUpdateBridge()
  })
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary label="root">
      <HapticsProvider>
        <HashRouter>
          <App />
        </HashRouter>
      </HapticsProvider>
    </ErrorBoundary>
  </StrictMode>
)
