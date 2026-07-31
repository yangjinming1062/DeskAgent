import './styles.css'

import { QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'

import { ErrorBoundary } from '@/shared/components/error-boundary'
import { HapticsProvider } from '@/shared/components/haptics-provider'
import { installClipboardShim } from '@/shared/lib/clipboard'
import { queryClient } from '@/shared/lib/query-client'
import { ThemeProvider } from '@/shared/themes/context'
import type { DesktopRunnerUpdateEvent, DesktopUpdateEvent } from '@/shared/types/global'

import App from './app'
import { setRunnerUpdateStatus, setUpdateStatus } from './hub/settings-store'

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
// Electron at startup. The toast renders this state to keep the user
// informed through the full lifecycle. See runner-updater.cjs for details.
window.deskagent?.update?.onRunnerEvent?.((payload: DesktopRunnerUpdateEvent) => {
  switch (payload.kind) {
    case 'runner-prefetching':
      setRunnerUpdateStatus({
        status: 'prefetching',
        version: payload.version,
        phase: payload.phase,
        percent: payload.percent
      })

      break

    case 'runner-ready':
      setRunnerUpdateStatus({ status: 'ready', version: payload.version })

      break

    case 'runner-installing':
      setRunnerUpdateStatus({
        status: 'installing',
        version: payload.version,
        phase: payload.phase,
        percent: payload.percent
      })

      break

    case 'runner-installed':
      setRunnerUpdateStatus({ status: 'installed', version: payload.version })

      break

    case 'runner-failed':
      setRunnerUpdateStatus({
        status: 'failed',
        error: payload.error,
        recoverable: payload.recoverable,
        version: payload.version
      })

      break
  }
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
