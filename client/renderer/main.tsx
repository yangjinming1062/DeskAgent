import './styles.css'

import { QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'

import { ErrorBoundary } from '@/shared/components/error-boundary'
import { HapticsProvider } from '@/shared/components/haptics-provider'
import { installClipboardShim } from '@/shared/lib/clipboard'
import { queryClient } from '@/shared/lib/query-client'
import type { DesktopUpdateEvent } from '@/shared/types/global'

import App from './app'
import { setUpdateStatus } from './hub/settings-store'

installClipboardShim()

// 启动时订阅 electron-updater 事件。主进程在启动后约 30 秒自动检查更新；
// 本监听器把所有事件泵入渲染层 store，
// 让状态栏徽标、关于面板和更新提示都能联动。
window.spiritagent?.update?.onEvent?.((payload: DesktopUpdateEvent) => {
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

// 订阅 Runner 侧的更新事件。第 1 阶段（预下载）在收到 `update-downloaded` 后的
// 旧版 Electron 中运行；第 2 阶段（安装）在新版 Electron 启动时运行。
// 桌面状态栏通过 $updateStatus 原子（见上方 main.tsx）暴露这些事件——
// Runner 内部的阶段切换刻意不在渲染层面向用户展示。
window.spiritagent?.update?.onRunnerEvent?.(() => {
  // 刻意留作空操作：完整生命周期见 runner-updater.cjs。
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary label="root">
      <QueryClientProvider client={queryClient}>
        <HapticsProvider>
          <HashRouter>
            <App />
          </HashRouter>
        </HapticsProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>
)
