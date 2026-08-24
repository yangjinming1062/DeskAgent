import './styles.css'

import type { DesktopUpdateEvent } from '@ipc/contracts'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'

import { ErrorBoundary } from '@/shared/components/error-boundary'
import { HapticsProvider } from '@/shared/components/haptics-provider'
import { installClipboardShim } from '@/shared/lib/clipboard'

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

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary label="root">
      <HapticsProvider>
        <HashRouter>
          <App />
        </HashRouter>
      </HapticsProvider>
    </ErrorBoundary>
  </StrictMode>
)
