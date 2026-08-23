import './styles.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { ClipDebugger } from '@/clip-debugger/clip-debugger'
import { ErrorBoundary } from '@/shared/components/error-boundary'
import { HapticsProvider } from '@/shared/components/haptics-provider'
import { installClipboardShim } from '@/shared/lib/clipboard'

installClipboardShim()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary label="clip-root">
      <HapticsProvider>
        <ClipDebugger />
      </HapticsProvider>
    </ErrorBoundary>
  </StrictMode>
)
