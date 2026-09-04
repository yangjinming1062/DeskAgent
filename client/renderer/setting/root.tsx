import { useStore } from '@nanostores/react'
import * as React from 'react'
import { lazy, Suspense, useEffect } from 'react'

import { NotificationStack } from '@/shared'
import { Loader2 } from '@/shared/lib/icons'
import { $auth, applyAuthBroadcast, hydrateAuth, logout } from '@/shared/store/auth'
import { hydrateRunnerStatus } from '@/shared/store/runner-status'

// 工具窗口仅在认证后承载设置面板；未认证时由精灵窗口处理激活流程。
// 纯 REST 模式——不启动网关。
const SettingsView = lazy(() => import('./settings').then(m => ({ default: m.SettingsView })))

export function SettingRoot(): React.JSX.Element {
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
    const off = window.spiritagent.onAuthChanged(payload => void applyAuthBroadcast(payload))

    return () => off()
  }, [])

  useEffect(() => {
    const off = window.spiritagent.onSessionExpired(() => void logout())

    return () => off()
  }, [])

  // 托盘菜单"退出登录"触发此 IPC；渲染进程是唯一能驱动应用内登出流程的地方
  useEffect(() => {
    const off = window.spiritagent.onTrayLogout?.(() => void logout())

    return () => off?.()
  }, [])

  // 激活在伙伴（精灵）窗口完成；工具窗口仅渲染 Settings，
  // 在认证前没有内容可显示。
  if (auth.kind !== 'authenticated') {
    return (
      <div className="fixed inset-0 z-[1300] flex items-center justify-center bg-black/60 text-strong backdrop-blur-md">
        <Loader2 className="size-8 animate-spin text-faint" />
      </div>
    )
  }

  // window.close() 走拦截器隐藏窗口而非销毁——设置面板是按需打开的。
  // lazy 加载空档用同款深色背板兜住，避免 body 闪一帧浅色。
  return (
    <>
      <Suspense
        fallback={
          <div className="fixed inset-0 z-[1300] flex items-center justify-center bg-black/60 text-strong backdrop-blur-md">
            <Loader2 className="size-8 animate-spin text-faint" />
          </div>
        }
      >
        <SettingsView onClose={() => window.close()} />
      </Suspense>
      <NotificationStack />
    </>
  )
}
