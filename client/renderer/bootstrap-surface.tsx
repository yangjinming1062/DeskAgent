import './styles.css'

import type React from 'react'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { initCompanionPrefsSync } from '@/companion'
import { ErrorBoundary } from '@/shared/components/error-boundary'
import { HapticsProvider } from '@/shared/components/haptics-provider'
import { applyNoBlurIfNeeded } from '@/shared/lib/apply-no-blur'
import { installClipboardShim } from '@/shared/lib/clipboard'
import { hydrateSurfaces } from '@/shared/store/surfaces'
import { initUiThemeSync } from '@/shared/store/theme'

export function bootstrapSurface(label: string, RootComponent: React.ComponentType): void {
  installClipboardShim()
  applyNoBlurIfNeeded()
  initUiThemeSync()
  initCompanionPrefsSync()
  hydrateSurfaces()

  const container = document.getElementById('root')

  if (!container) {
    throw new Error(`${label}: missing #root element`)
  }

  createRoot(container).render(
    <StrictMode>
      <ErrorBoundary label={label}>
        <HapticsProvider>
          <RootComponent />
        </HapticsProvider>
      </ErrorBoundary>
    </StrictMode>
  )
}
