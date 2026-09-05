import './styles.css'

import type React from 'react'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'

import { initCompanionPrefsSync, initPersonaSkin } from '@/companion'
import { hydratePersona } from '@/companion/persona-store'
import { hydratePortrait } from '@/companion/portrait-store'
import { useAuthBridge } from '@/companion/use-auth-bridge'
import { ErrorBoundary } from '@/shared/components/error-boundary'
import { HapticsProvider } from '@/shared/components/haptics-provider'
import { applyNoBlurIfNeeded } from '@/shared/lib/apply-no-blur'
import { installClipboardShim } from '@/shared/lib/clipboard'
import { hydrateSurfaces } from '@/shared/store/surfaces'
import { initUiThemeSync } from '@/shared/store/theme'

function SurfaceAuthBootstrap(): null {
  useAuthBridge()

  return null
}

export function bootstrapSurface(label: string, RootComponent: React.ComponentType): void {
  installClipboardShim()
  applyNoBlurIfNeeded()
  initUiThemeSync()
  initCompanionPrefsSync()
  hydrateSurfaces()
  void hydratePersona()
  void hydratePortrait()
  initPersonaSkin()

  const container = document.getElementById('root')

  if (!container) {
    throw new Error(`${label}: missing #root element`)
  }

  createRoot(container).render(
    <StrictMode>
      <ErrorBoundary label={label}>
        <HapticsProvider>
          <HashRouter>
            <SurfaceAuthBootstrap />
            <RootComponent />
          </HashRouter>
        </HapticsProvider>
      </ErrorBoundary>
    </StrictMode>
  )
}
