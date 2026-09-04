import { useStore } from '@nanostores/react'
import type React from 'react'
import { type RefObject, useEffect, useRef, useState } from 'react'

import { attachVideoFile } from '@/chat/chat-attach-picker'
import type { ConversationVariant } from '@/chat/chat-dock-message-bubble'
import {
  $chatDraftFromUndo,
  $chatMessageList,
  $chatSessionId,
  $chatTurnInFlight,
  $lastAssistantStreaming,
  $pendingExternalAttachment,
  $pendingPromptBatch,
  clearExternalAttachment
} from '@/chat/chat-store'
import { useChatSubmit } from '@/chat/use-chat-submit'
import { $persona } from '@/companion'
import { useVoiceRecorder } from '@/companion/hooks/use-voice-recorder'
import { ConversationInput } from '@/conversation/conversation-input'
import { ConversationSurface } from '@/conversation/conversation-surface'
import { resolveDroppedFiles } from '@/shared/lib/file-drop'
import { cn } from '@/shared/lib/utils'
import { notify } from '@/shared/store/notifications'

export interface ChatPanelProps {
  className?: string
  gatewayState: string
  inputWrapperClassName?: string
  isReadOnlySession: boolean
  scrollRef: RefObject<HTMLDivElement | null>
  surfaceClassName?: string
  variant: ConversationVariant
}

export function ChatPanel({
  className,
  gatewayState,
  inputWrapperClassName,
  isReadOnlySession,
  scrollRef,
  surfaceClassName,
  variant
}: ChatPanelProps): React.JSX.Element {
  const chatSessionId = useStore($chatSessionId)
  const list = useStore($chatMessageList)
  const turnInFlight = useStore($chatTurnInFlight)
  const lastStreaming = useStore($lastAssistantStreaming)
  const pendingBatchLen = useStore($pendingPromptBatch).length
  const [attachMenuOpen, setAttachMenuOpen] = useState(false)
  const [, setExternalPathCount] = useState(0)
  const externalPathsRef = useRef<string[]>([])
  const attachMenuRef = useRef<HTMLDivElement>(null)
  const lastListLenRef = useRef(list.length)

  const submit = useChatSubmit({
    externalPathsRef,
    gatewayState,
    isReadOnlySession,
    onClearExternalPaths: () => setExternalPathCount(0),
    onPreCheckFail: msg => notify({ kind: 'warning', message: msg })
  })

  const { text, setText, pending, setPending, sending, send, handleStop } = submit
  const { recording, start: startRecording, stop: stopRecording } = useVoiceRecorder({ isReadOnlySession })

  const isGenerating = gatewayState === 'open' && (sending || pendingBatchLen > 0 || turnInFlight || lastStreaming)

  // 外部文件投喂与常规附件合并
  useEffect(() => {
    return $pendingExternalAttachment.listen(state => {
      if (!state || state.paths.length === 0) {
        return
      }

      externalPathsRef.current = [...externalPathsRef.current, ...state.paths]
      setExternalPathCount(externalPathsRef.current.length)
      clearExternalAttachment()
      notify({ kind: 'info', message: `收到 ${state.paths.length} 个文件` })
    })
  }, [])

  // 撤销草稿回填，多窗口按会话过滤
  useEffect(() => {
    return $chatDraftFromUndo.listen(draft => {
      if (!draft || draft.session_id !== chatSessionId) {
        return
      }

      setText(draft.text)
      $chatDraftFromUndo.set(null)
    })
  }, [chatSessionId, setText])

  // 批量消息变更触发滚动到底
  useEffect(() => {
    if (list.length === lastListLenRef.current) {
      return
    }

    lastListLenRef.current = list.length
    const el = scrollRef.current

    if (el) {
      el.scrollTo?.({ top: el.scrollHeight, behavior: 'smooth' })
    }
  }, [list.length, scrollRef])

  const submitState = {
    gatewayState: gatewayState as 'open' | 'closed' | 'connecting',
    isGenerating,
    isReadOnlySession,
    pending,
    recording,
    sending,
    text
  }

  const persona = useStore($persona)
  const companionName = persona?.name || '伙伴'
  const isOnline = gatewayState === 'open'
  const isConnecting = gatewayState === 'connecting'
  const statusText = isOnline ? '在线' : isConnecting ? '连接中' : '未连接'
  const statusColor = isOnline ? 'text-emerald-400' : isConnecting ? 'text-amber-400' : 'text-white/40'

  const statusDot = isOnline
    ? 'bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.8)]'
    : isConnecting
      ? 'bg-amber-400'
      : 'bg-white/30'

  return (
    <div className={cn('flex flex-col h-full min-h-0', className)}>
      {variant === 'workbench' && (
        <div className="flex items-center justify-between border-b border-white/8 px-4 py-2.5 bg-surface-chrome/20 shrink-0">
          <div className="flex items-center gap-2">
            <span className="size-2 rounded-full bg-blue-500 shadow-[0_0_8px_rgba(59,130,246,0.9)]" />
            <span className="text-xs font-semibold text-white tracking-wide">{companionName}</span>
            <span className={cn('flex items-center gap-1 text-[10.5px] font-medium', statusColor)}>
              <span className={cn('size-1.5 rounded-full', statusDot)} />
              <span>{statusText}</span>
            </span>
          </div>
        </div>
      )}
      <ConversationSurface className={surfaceClassName} scrollRef={scrollRef} variant={variant} />
      <div className={inputWrapperClassName}>
        <ConversationInput
          attachMenuOpen={attachMenuOpen}
          externalPathsRef={externalPathsRef}
          onAttachMenuToggle={setAttachMenuOpen}
          onDrop={e => {
            const paths = resolveDroppedFiles(e.dataTransfer?.files)

            if (paths.length === 0) {
              return
            }

            e.preventDefault()
            externalPathsRef.current = [...externalPathsRef.current, ...paths]
            setExternalPathCount(externalPathsRef.current.length)
            notify({ kind: 'info', message: `添加了 ${paths.length} 个附件` })
          }}
          onPaste={async e => {
            const files = Array.from(e.clipboardData?.files ?? [])

            if (files.length === 0) {
              return
            }

            e.preventDefault()

            for (const file of files) {
              if (file.type.startsWith('image/')) {
                const dataUrl = await new Promise<string | null>(resolve => {
                  const reader = new FileReader()

                  reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : null)
                  reader.onerror = () => resolve(null)
                  reader.readAsDataURL(file)
                })

                if (dataUrl) {
                  setPending({ type: 'image', value: dataUrl, fileName: file.name })
                }

                continue
              }

              let filePath = (file as File & { path?: string }).path

              if (!filePath && window.spiritagentWebUtils) {
                try {
                  filePath = window.spiritagentWebUtils.getPathForFile(file)
                } catch {
                  filePath = undefined
                }
              }

              if (filePath) {
                if (file.type.startsWith('video/')) {
                  await attachVideoFile(filePath, setPending)
                } else {
                  externalPathsRef.current = [...externalPathsRef.current, filePath]
                  setExternalPathCount(externalPathsRef.current.length)
                }
              }
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
          variant={variant}
        />
      </div>
    </div>
  )
}

// ConversationInput 的 gatewayState 期望字面量类型；上面用字符串避免耦合。
// 此处只断言"是三个之一"，运行时真实值由 $gatewayState 提供。
export type GatewayStateLiteral = 'closed' | 'connecting' | 'open'
