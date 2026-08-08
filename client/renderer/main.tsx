import './styles.css'

import { QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'

import { ErrorBoundary } from '@/shared/components/error-boundary'
import { HapticsProvider } from '@/shared/components/haptics-provider'
import { installClipboardShim } from '@/shared/lib/clipboard'
import { queryClient } from '@/shared/lib/query-client'
import { ThemeProvider } from '@/shared/themes'
import type { DesktopUpdateEvent } from '@/shared/types/global'

import App from './app'
import { setUpdateStatus } from './hub/settings-store'

installClipboardShim()

// Subscribe to electron-updater events at boot. The main process auto-checks
// ~30s after launch; this listener pumps every event into the renderer store
// so the status bar badge, About panel, and update toast all react.
window.deskagent?.update?.onEvent?.((payload: DesktopUpdateEvent) => {
  switch (payload.type) {
    case 'checking':
      setUpdateStatus({ status: 'checking' })

      break

    case 'available':
      setUpdateStatus({
        status: 'available',
        version: payload.info?.version ?? '',
        releaseDate: payload.info?.releaseDate
      })

      break

    case 'none':
      setUpdateStatus({ status: 'none', version: payload.info?.version })

      break

    case 'progress':
      setUpdateStatus({
        status: 'downloading',
        percent: payload.progress?.percent ?? 0,
        transferred: payload.progress?.transferred ?? 0,
        total: payload.progress?.total ?? 0
      })

      break

    case 'downloaded':
      setUpdateStatus({ status: 'downloaded', version: payload.info?.version ?? '' })

      break

    case 'error':
      setUpdateStatus({ status: 'error', message: payload.message ?? 'Unknown error' })

      break
  }
})

// Subscribe to runner-side update events. Phase 1 (prefetch) runs in the OLD
// Electron after `update-downloaded`; phase 2 (install) runs in the NEW
// Electron at startup. The desktop status bar surfaces these via the
// $updateStatus atom (handled in main.tsx above) — runner-internal phase
// transitions are intentionally not user-visible in the renderer.
window.deskagent?.update?.onRunnerEvent?.(() => {
  // Intentionally a no-op: see runner-updater.cjs for the full lifecycle.
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary label="root">
      <QueryClientProvider client={queryClient}>
        <ThemeProvider>
          <HapticsProvider>
            <HashRouter>
              <App />
            </HashRouter>
          </HapticsProvider>
        </ThemeProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>
)
