import { useStore } from '@nanostores/react'
import { lazy, Suspense, useEffect } from 'react'

import { Loader2 } from '@/shared/lib/icons'
import { $auth, applyAuthBroadcast, hydrateAuth, logout } from '@/shared/store/auth'
import { hydrateRunnerStatus } from '@/shared/store/runner-status'

// The framed tool window hosts Settings only (post-authentication). When
// unauthenticated, the companion (sprite) window handles activation — the
// tool window simply waits. REST-only — it never boots the gateway, so the
// MCP reload button (which needs the gateway) degrades gracefully.
// `gateway={null}` encodes that.
const SettingsView = lazy(() => import('./settings').then(m => ({ default: m.SettingsView })))

export function ToolRoot(): React.JSX.Element {
  const auth = useStore($auth)

  useEffect(() => {
    void hydrateAuth()
  }, [])

  // Hydrate runner-status atom so hub-side consumers (speech-settings.tsx
  // probes local engine availability) can subscribe to phase transitions
  // without their own sync-getter.
  useEffect(() => {
    void hydrateRunnerStatus()
  }, [])

  useEffect(() => {
    const off = window.spiritagent.onAuthChanged(payload => applyAuthBroadcast(payload))

    return () => off()
  }, [])

  useEffect(() => {
    const off = window.spiritagent.onSessionExpired(() => void logout())

    return () => off()
  }, [])

  // Tray menu "Log out" entry fires this bridge; subscribe so clicking the
  // tray item actually logs the user out (the menu fires this IPC and the
  // renderer is the only place that can drive the in-app logout flow).
  useEffect(() => {
    const off = window.spiritagent.onTrayLogout?.(() => void logout())

    return () => off?.()
  }, [])

  // Activation happens in the companion (sprite) window; the tool window
  // only renders Settings and has nothing to show until authenticated.
  if (auth.kind !== 'authenticated') {
    return (
      <div className="fixed inset-0 z-[1300] flex items-center justify-center bg-(--ui-chat-surface-background)">
        <Loader2 className="size-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  // window.close() hits the close interceptor (tray.cjs) which hides the
  // tool window rather than destroying it — Settings is on-demand.
  return (
    <Suspense fallback={null}>
      <SettingsView gateway={null} onClose={() => window.close()} />
    </Suspense>
  )
}
