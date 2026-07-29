import { useStore } from '@nanostores/react'
import { Suspense, lazy, useEffect } from 'react'

import { Loader2 } from '@/lib/icons'
import { $auth, applyAuthBroadcast, hydrateAuth, logout } from '@/store/auth'

import { LoginPage } from './login/login-page'

// The framed tool window hosts Login (unauthenticated) or Settings
// (authenticated); the renderer self-selects from $auth. REST-only — it never
// boots the gateway, so the MCP reload button (which needs the gateway) degrades
// gracefully. `gateway={null}` encodes that.
const SettingsView = lazy(() => import('./settings').then(m => ({ default: m.SettingsView })))

export function ToolRoot() {
  const auth = useStore($auth)

  useEffect(() => {
    void hydrateAuth()
  }, [])

  useEffect(() => {
    const off = window.deskagent.onAuthChanged(payload => applyAuthBroadcast(payload))
    return () => off()
  }, [])

  useEffect(() => {
    const off = window.deskagent.onSessionExpired(() => void logout())
    return () => off()
  }, [])

  if (auth.kind === 'pending') {
    return (
      <div className="fixed inset-0 z-[1300] flex items-center justify-center bg-(--ui-chat-surface-background)">
        <Loader2 className="size-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (auth.kind === 'unauthenticated') {
    return <LoginPage />
  }

  // window.close() hits the close interceptor (tray.cjs) which hides the
  // tool window rather than destroying it — Settings is on-demand.
  return (
    <Suspense fallback={null}>
      <SettingsView gateway={null} onClose={() => window.close()} />
    </Suspense>
  )
}
