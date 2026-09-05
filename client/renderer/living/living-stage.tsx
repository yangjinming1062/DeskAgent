// 生活空间右栏：根据 living-view 切换内容。
//
// - chat: ChatPanel（生活空间变体的对话 + 输入；与工作台共用）
// - wardrobe: WardrobePage（衣橱）
// - appearance: AppearancePage（形象 / 渲染模式）
// - moments / diary: 后端直连两页
// - channels / room: 单文件页
// - settings: LivingSettings（长页：角色/音色/交互/主题/语音/快捷键/关于）

import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { ChatPanel } from '@/chat/chat-panel'
import { $chatSessionId, $chatSessionKind } from '@/chat/chat-store'
import {
  $currentSessionKind,
  $sessions,
  isCompanionSession,
  openMainSession,
  switchSession
} from '@/chat/session-list-store'
import { ArrowRight } from '@/shared/lib/icons'
import { $gatewayState } from '@/shared/store/gateway'
import { requestOpenSurface } from '@/shared/store/surfaces'

import { AppearancePage } from './appearance-page'
import { ChannelsPage } from './channels-page'
import { DiaryPage } from './diary-page'
import { $livingView, type LivingView } from './living-store'
import styles from './living.module.css'
import { MomentsPage } from './moments-page'
import { RoomPage } from './room-page'
import { LivingSettings } from './settings/living-settings'
import { WardrobePage } from './wardrobe-page'

// 视图 → 渲染组件的闭包表（`chat` 走 ChatStage 局部组件，其他直接挂页）。
// Record<LivingView, …> 强制 LivingView 出现新成员时报缺 key 错。
const VIEW_RENDERERS: Record<LivingView, () => React.JSX.Element> = {
  appearance: () => <AppearancePage />,
  channels: () => <ChannelsPage />,
  chat: () => <ChatStage />,
  diary: () => <DiaryPage />,
  moments: () => <MomentsPage />,
  room: () => <RoomPage />,
  settings: () => <LivingSettings />,
  wardrobe: () => <WardrobePage />
}

export function LivingStage(): React.JSX.Element {
  const view = useStore($livingView)

  return VIEW_RENDERERS[view]()
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
