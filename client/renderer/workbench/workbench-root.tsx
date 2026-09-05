// 工作台根组件：三栏 + 顶栏 + 顶栏抽屉形态的工位设置。
//
// 三栏：会话侧栏（256） / 对话（flex） / Run Rail（320，可折到 0）；
// 顶栏：会话名 + 工位环境 + 回生活空间；工位设置抽屉从顶部下拉展开。

import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { ChatPanel } from '@/chat/chat-panel'
import { $chatSessionId, $chatSessionKind } from '@/chat/chat-store'
import {
  $currentSessionKind,
  $currentSessionTitle,
  $sessions,
  isCompanionSession,
  switchSession
} from '@/chat/session-list-store'
import { Home, SlidersHorizontal, Terminal } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { WindowControls } from '@/shared/panel'
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

  const isStationSettingsHash = (rawHash: string): boolean => {
    const clean = rawHash.replace(/^#\/?/, '').split('?')[0].toLowerCase()

    return (
      clean.includes('settings') ||
      clean.startsWith('inference') ||
      clean.startsWith('runner') ||
      clean.startsWith('skills') ||
      clean.startsWith('station')
    )
  }

  const [settingsOpen, setSettingsOpen] = useState(() => {
    if (typeof window !== 'undefined' && window.location.hash) {
      return isStationSettingsHash(window.location.hash)
    }

    return false
  })

  // 保证工作台处于有效工作会话下（不处于生活空间的「陪伴」会话下，且空会话时自动定位到开发工位）
  useEffect(() => {
    if (sessions.length === 0) {
      return
    }

    const current = sessions.find(s => s.id === currentSessionId)

    if (!current || isCompanionSession(current)) {
      const workTarget =
        sessions.find(s => s.system_preset_id === 'developer') ?? sessions.find(s => !isCompanionSession(s))

      if (workTarget && workTarget.id !== currentSessionId) {
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
      if (isStationSettingsHash(window.location.hash)) {
        setSettingsOpen(true)
      }
    }

    window.addEventListener('hashchange', onHash)

    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const clearSettingsHash = (): void => {
    if (typeof window !== 'undefined' && isStationSettingsHash(window.location.hash)) {
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
      <header
        className={styles.titlebar}
        onDoubleClick={() => {
          void window.spiritagent?.surface?.maximize?.()
        }}
      >
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
            title={settingsOpen ? '收起工位环境' : '工位环境配置'}
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
            title="切换到生活空间"
            type="button"
          >
            <Home size={13} />
            <span>生活空间</span>
          </button>
          <WindowControls />
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
            className="flex-1 min-h-0"
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
