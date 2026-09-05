import './styles.css'

import { useStore } from '@nanostores/react'
import type React from 'react'
import { StrictMode, useEffect } from 'react'
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
import { $auth } from '@/shared/store/auth'
import { hydrateSurfaces } from '@/shared/store/surfaces'
import { initUiThemeSync } from '@/shared/store/theme'

function SurfaceAuthBootstrap(): null {
  useAuthBridge()
  const auth = useStore($auth)

  useEffect(() => {
    if (auth.kind !== 'authenticated') {
      return
    }

    void hydratePersona()
    void hydratePortrait()

    const onFocus = (): void => {
      void hydratePersona({ silent: true })
      void hydratePortrait()
    }

    window.addEventListener('focus', onFocus)

    return () => {
      window.removeEventListener('focus', onFocus)
    }
  }, [auth.kind])

  return null
}

export function bootstrapSurface(label: string, RootComponent: React.ComponentType): void {
  installClipboardShim()
  applyNoBlurIfNeeded()
  initUiThemeSync()
  initCompanionPrefsSync()
  hydrateSurfaces()
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
