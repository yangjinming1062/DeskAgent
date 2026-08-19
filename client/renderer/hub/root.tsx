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

  // 水合 runner-status 原子，让枢纽侧消费者（如 speech-settings.tsx
  // 探测本地引擎可用性）能订阅阶段变化，
  // 不必各自实现同步 getter。
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

  // 激活在伙伴（精灵）窗口完成；工具窗口仅渲染 Settings，
  // 在认证前没有内容可显示。
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
