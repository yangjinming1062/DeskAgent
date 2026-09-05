// 生活空间右栏：根据 living-view 切换内容。
//
// - chat: ChatPanel（生活空间变体的对话 + 输入；与工作台共用）
// - wardrobe: OutfitPanel（现有换一身/形象）
// - appearance / settings / channels: AppSettingsPanel，由 setLivingView 同步 appSettingsView
// - moments / diary: 后端直连两页

import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { $chatSessionId, $chatSessionKind } from '@/chat/chat-store'
import {
  $currentSessionKind,
  $sessions,
  isCompanionSession,
  openMainSession,
  switchSession
} from '@/chat/session-list-store'
import { ChatPanel } from '@/conversation/chat-panel'
import { AppearancePage, AppSettingsPanel, ChannelsSettings, WardrobePage } from '@/setting'
import { ArrowRight } from '@/shared/lib/icons'
import { $gatewayState } from '@/shared/store/gateway'
import { requestOpenSurface } from '@/shared/store/surfaces'

import { DiaryPage } from './diary-page'
import { $livingView } from './living-store'
import styles from './living.module.css'
import { MomentsPage } from './moments-page'

export function LivingStage(): React.JSX.Element {
  const view = useStore($livingView)

  if (view === 'chat') {
    return <ChatStage />
  }

  if (view === 'wardrobe') {
    return <WardrobePage />
  }

  if (view === 'appearance') {
    return <AppearancePage />
  }

  if (view === 'channels') {
    return <ChannelsSettings />
  }

  if (view === 'settings') {
    return <AppSettingsPanel />
  }

  if (view === 'moments') {
    return <MomentsPage />
  }

  if (view === 'diary') {
    return <DiaryPage />
  }

  return (
    <main className={styles.placeholder}>
      <h1 className={styles.placeholderTitle}>{view}</h1>
      <p className={styles.placeholderHint}>该视图待接入。</p>
    </main>
  )
}

function ChatStage(): React.JSX.Element {
  const chatSessionId = useStore($chatSessionId)
  const chatSessionKind = useStore($chatSessionKind)
  const currentSessionKind = useStore($currentSessionKind)
  const sessions = useStore($sessions)
  const gatewayState = useStore($gatewayState)
  const [workBannerDismissed, setWorkBannerDismissed] = useState(false)

  const sessionKind = chatSessionKind || currentSessionKind || ''
  const isReadOnlySession = sessionKind === 'im'
  const scrollRef = useRef<HTMLDivElement>(null)

  const currentSession = sessions.find(s => s.id === chatSessionId)
  const isWorkSession = currentSession ? !isCompanionSession(currentSession) : false

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const sid = params.get('sessionId')

    if (sid) {
      void switchSession(sid)
    } else {
      void openMainSession()
    }
  }, [])

  return (
    <div className={styles.chatStage}>
      {isWorkSession && !workBannerDismissed && (
        <div className="mx-4 mt-3 flex items-center justify-between rounded-xl border border-line-hairline bg-surface-card/75 px-3 py-2 text-xs text-strong shadow-xs backdrop-blur-md">
          <div className="flex items-center gap-2">
            <span className="size-1.5 rounded-full bg-accent" />
            <span>这是工作会话，要去工作台吗？</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              className="text-[11px] text-faint transition hover:text-strong"
              onClick={() => setWorkBannerDismissed(true)}
              type="button"
            >
              仍在这里聊
            </button>
            <button
              className="inline-flex items-center gap-1 rounded-lg bg-accent-soft px-2.5 py-1 text-[11px] font-medium text-strong transition hover:bg-accent-soft/80"
              onClick={() => {
                void requestOpenSurface('workbench', chatSessionId ? { sessionId: chatSessionId } : {})
              }}
              type="button"
            >
              <span>去工作台</span>
              <ArrowRight className="size-3" />
            </button>
          </div>
        </div>
      )}
      <ChatPanel
        gatewayState={gatewayState}
        inputWrapperClassName={styles.chatInputWrapper}
        isReadOnlySession={isReadOnlySession}
        scrollRef={scrollRef}
        surfaceClassName={styles.chatSurface}
        variant="living"
      />
    </div>
  )
}
