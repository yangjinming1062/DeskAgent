// 生活空间右栏：根据 living-view 切换内容。
//
// - chat: ConversationSurface + ConversationInput（与工作台共用）
// - wardrobe: OutfitPanel（现有换一身/形象）
// - appearance / settings / channels: AppSettingsPanel，由 setLivingView 同步 appSettingsView
// - moments / diary: 后端直连两页

import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { $chatSessionKind } from '@/chat/chat-store'
import { $currentSessionKind, openMainSession } from '@/chat/session-list-store'
import { useChatSubmit } from '@/chat/use-chat-submit'
import { useVoiceRecorder } from '@/companion/hooks/use-voice-recorder'
import { ConversationInput, ConversationSurface } from '@/conversation'
import { AppearancePage, AppSettingsPanel, ChannelsSettings, WardrobePage } from '@/setting'
import { resolveDroppedFiles } from '@/shared/lib/file-drop'
import { $gatewayState } from '@/shared/store/gateway'

import { DiaryPage } from './diary-page'
import { $livingView, setLivingView } from './living-store'
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
    return <AppSettingsPanel onClose={() => setLivingView('chat')} />
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
  const chatSessionKind = useStore($chatSessionKind)
  const currentSessionKind = useStore($currentSessionKind)
  const gatewayState = useStore($gatewayState)

  const sessionKind = chatSessionKind || currentSessionKind || ''
  const isReadOnlySession = sessionKind === 'im'

  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const attachMenuRef = useRef<HTMLDivElement>(null)
  const externalPathsRef = useRef<string[]>([])
  const [attachMenuOpen, setAttachMenuOpen] = useState(false)

  // 进入生活空间陪伴对话，强制挂载陪伴主会话
  useEffect(() => {
    void openMainSession()
  }, [])

  const submit = useChatSubmit({
    externalPathsRef,
    gatewayState,
    isReadOnlySession,
    onClearExternalPaths: () => undefined,
    onPreCheckFail: () => undefined
  })

  const { text, setText, pending, setPending, sending, send, handleStop } = submit
  const { recording, start: startRecording, stop: stopRecording } = useVoiceRecorder({ isReadOnlySession })

  const submitState = {
    gatewayState,
    isGenerating: gatewayState === 'open' && sending,
    isReadOnlySession,
    pending,
    recording,
    sending,
    text
  }

  return (
    <div className={styles.chatStage}>
      <ConversationSurface className={styles.chatSurface} scrollRef={scrollRef} />
      <div className={styles.chatInputWrapper}>
        <ConversationInput
          attachMenuOpen={attachMenuOpen}
          externalPathsRef={externalPathsRef}
          inputRef={inputRef}
          onAttachMenuToggle={setAttachMenuOpen}
          onDrop={e => {
            const paths = resolveDroppedFiles(e.dataTransfer?.files)

            if (paths.length > 0) {
              e.preventDefault()
            }
          }}
          onRecordingPointerCancel={e => {
            if (e.currentTarget.hasPointerCapture(e.pointerId)) {
              e.currentTarget.releasePointerCapture(e.pointerId)
            }

            void stopRecording()
          }}
          onRecordingPointerDown={e => {
            e.currentTarget.setPointerCapture(e.pointerId)
            void startRecording()
          }}
          onRecordingPointerUp={e => {
            if (e.currentTarget.hasPointerCapture(e.pointerId)) {
              e.currentTarget.releasePointerCapture(e.pointerId)
            }

            void stopRecording()
          }}
          onSend={() => {
            void send()
          }}
          onSetPending={setPending}
          onSetText={setText}
          onStop={handleStop}
          setAttachMenuRef={attachMenuRef}
          submit={submitState}
        />
      </div>
    </div>
  )
}
