// 工作台根组件：三栏 + 顶栏 + 顶栏抽屉形态的工位设置。
//
// 三栏：会话侧栏（256） / 对话（flex） / Run Rail（320，可折到 0）；
// 顶栏：会话名 + 工位环境 + 回生活空间；工位设置抽屉从顶部下拉展开。

import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { $chatSessionId, $chatSessionKind } from '@/chat/chat-store'
import {
  $currentSessionKind,
  $currentSessionTitle,
  $sessions,
  isCompanionSession,
  switchSession
} from '@/chat/session-list-store'
import { ChatPanel } from '@/conversation/chat-panel'
import { Home, SlidersHorizontal, Terminal } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { $gatewayState } from '@/shared/store/gateway'
import { requestOpenSurface } from '@/shared/store/surfaces'

import { RunRail } from './run-rail'
import { SessionSidebar } from './session-sidebar'
import { StationSettings } from './station-settings'
import styles from './workbench.module.css'

export function WorkbenchRoot(): React.JSX.Element {
  const title = useStore($currentSessionTitle)
  const gatewayState = useStore($gatewayState)
  const chatSessionKind = useStore($chatSessionKind)
  const currentSessionKind = useStore($currentSessionKind)
  const currentSessionId = useStore($chatSessionId)
  const sessions = useStore($sessions)
  const sessionKind = chatSessionKind || currentSessionKind || ''
  const isReadOnlySession = sessionKind === 'im'

  const scrollRef = useRef<HTMLDivElement>(null)

  const [settingsOpen, setSettingsOpen] = useState(() => {
    if (typeof window !== 'undefined' && window.location.hash) {
      return window.location.hash.toLowerCase().includes('settings')
    }

    return false
  })

  // 保证工作台不处于生活空间的「陪伴」会话下
  useEffect(() => {
    const current = sessions.find(s => s.id === currentSessionId)

    if (isCompanionSession(current)) {
      const workTarget = sessions.find(s => s.system_preset_id === 'developer' || !isCompanionSession(s))

      if (workTarget) {
        void switchSession(workTarget.id)
      }
    }
  }, [sessions, currentSessionId])

  useEffect(() => {
    if (typeof window !== 'undefined' && window.location.search) {
      const params = new URLSearchParams(window.location.search)
      const sid = params.get('sessionId')

      if (sid) {
        void switchSession(sid)
      }
    }

    const onHash = (): void => {
      if (window.location.hash.toLowerCase().includes('settings')) {
        setSettingsOpen(true)
      }
    }

    window.addEventListener('hashchange', onHash)

    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const clearSettingsHash = (): void => {
    if (typeof window !== 'undefined' && window.location.hash.toLowerCase().includes('settings')) {
      window.history.replaceState(null, '', window.location.pathname + window.location.search)
    }
  }

  const toggleSettings = (): void => {
    setSettingsOpen(open => {
      if (open) {
        clearSettingsHash()
      }

      return !open
    })
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape' && settingsOpen) {
        clearSettingsHash()
        setSettingsOpen(false)
      }
    }

    window.addEventListener('keydown', onKey)

    return () => window.removeEventListener('keydown', onKey)
  }, [settingsOpen])

  return (
    <div className={styles.shell} data-surface="workbench">
      <header className={styles.titlebar}>
        <div className={styles.titleArea}>
          <Terminal className={styles.titleIcon} size={18} />
          <h1 className={styles.brandTitle}>SpiritAgent · 工作台</h1>
          <div className={styles.sessionBadge} title={title}>
            <span>{title || '工作工位'}</span>
          </div>
        </div>

        <div className={styles.actionsArea}>
          <button
            className={cn(styles.glassButton, settingsOpen && styles.glassButtonActive)}
            onClick={toggleSettings}
            type="button"
          >
            <SlidersHorizontal size={13} />
            <span>{settingsOpen ? '收起工位' : '工位环境'}</span>
          </button>
          <button
            className={styles.glassButton}
            onClick={() => {
              void requestOpenSurface('living')
            }}
            type="button"
          >
            <Home size={13} />
            <span>回生活空间</span>
          </button>
        </div>
      </header>

      {settingsOpen && (
        <div className={styles.drawerOverlay}>
          <div className="flex items-center justify-between border-b border-line-standard px-5 py-3">
            <h2 className="text-xs font-semibold text-strong">工位环境配置与执行器</h2>
            <button
              className="rounded-lg px-2 py-1 text-xs text-muted hover:bg-white/10 hover:text-strong"
              onClick={toggleSettings}
              type="button"
            >
              完成
            </button>
          </div>
          <div className="p-2">
            <StationSettings />
          </div>
        </div>
      )}

      <div className={styles.body}>
        <div className={styles.sidebarArea}>
          <SessionSidebar />
        </div>

        <main className={styles.center}>
          <ChatPanel
            gatewayState={gatewayState}
            inputWrapperClassName={styles.chatInputWrapper}
            isReadOnlySession={isReadOnlySession}
            scrollRef={scrollRef}
            surfaceClassName={styles.chatSurface}
            variant="workbench"
          />
        </main>

        <RunRail />
      </div>
    </div>
  )
}
