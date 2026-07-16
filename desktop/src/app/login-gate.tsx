import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import { Loader2 } from '@/lib/icons'
import { $auth, hydrateAuth, logout } from '@/store/auth'

import { DesktopController } from './desktop-controller'
import { LoginPage } from './login/login-page'

export function LoginGate() {
  const auth = useStore($auth)

  useEffect(() => {
    void hydrateAuth()
  }, [])

  // Auto-logout when the backend rejects our token (expired or revoked).
  // Subscribe unconditionally — the IPC is fire-and-forget, so an event that
  // arrives during the `pending` hydrate window would otherwise be lost.
  useEffect(() => {
    const off = window.zastDesktop.onSessionExpired(() => void logout())

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

  return <DesktopController />
}
