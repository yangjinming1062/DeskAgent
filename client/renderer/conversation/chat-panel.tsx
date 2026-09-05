import { useStore } from '@nanostores/react'
import type React from 'react'
import { type RefObject, useEffect, useRef, useState } from 'react'

import { attachVideoFile } from '@/chat/chat-attach-picker'
import type { ConversationVariant } from '@/chat/chat-dock-message-bubble'
import { ChatParamsPanel } from '@/chat/chat-params-panel'
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
import { ChatContextAmbientLine, ChatContextCapsule } from '@/chat/context-progress-bar'
import { useChatSubmit } from '@/chat/use-chat-submit'
import { $persona } from '@/companion'
import { useVoiceRecorder } from '@/companion/hooks/use-voice-recorder'
import { ConversationInput } from '@/conversation/conversation-input'
import { ConversationSurface } from '@/conversation/conversation-surface'
import { resolveDroppedFiles } from '@/shared/lib/file-drop'
import { SlidersHorizontal } from '@/shared/lib/icons'
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
  const [paramsPanelOpen, setParamsPanelOpen] = useState(false)
  const [paramsPanelTab, setParamsPanelTab] = useState<'context' | 'params'>('context')
  const paramsPanelRef = useRef<HTMLDivElement>(null)

  // 点击面板外部 / ESC 关闭参数面板
  useEffect(() => {
    if (!paramsPanelOpen) {
      return
    }

    const handlePointerDown = (e: PointerEvent) => {
      const target = e.target as Node | null

      if (!target || !paramsPanelRef.current?.contains(target)) {
        setParamsPanelOpen(false)
      }
    }

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setParamsPanelOpen(false)
      }
    }

    window.addEventListener('pointerdown', handlePointerDown)
    window.addEventListener('keydown', handleKeyDown)

    return () => {
      window.removeEventListener('pointerdown', handlePointerDown)
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [paramsPanelOpen])

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

  const headerWrapClass = cn(
    'relative flex items-center justify-between border-b shrink-0',
    variant === 'workbench'
      ? 'border-white/8 px-4 py-2.5 bg-surface-chrome/20'
      : 'border-line-hairline/40 px-3.5 py-2 bg-transparent'
  )

  const openParamsPanel = (tab: 'context' | 'params'): void => {
    setParamsPanelTab(tab)
    setParamsPanelOpen(true)
  }

  return (
    <div className={cn('flex flex-col flex-1 h-full min-h-0', className)}>
      <div className={headerWrapClass}>
        <div className="flex items-center gap-2">
          <span
            className={cn(
              'size-2 rounded-full bg-blue-500 shadow-[0_0_8px_rgba(59,130,246,0.9)]',
              variant === 'living' && 'size-1.5'
            )}
          />
          <span
            className={cn(
              'font-semibold tracking-wide',
              variant === 'workbench' ? 'text-xs text-white' : 'text-[11.5px] text-strong/90'
            )}
          >
            {companionName}
          </span>
          <span
            className={cn(
              'flex items-center gap-1 font-medium',
              variant === 'workbench' ? 'text-[10.5px]' : 'text-[10px] opacity-80',
              statusColor
            )}
          >
            <span className={cn('size-1.5 rounded-full', statusDot)} />
            <span>{statusText}</span>
          </span>
        </div>
        <div
          className="flex items-center gap-1.5"
          onPointerDown={e => {
            // 阻止冒泡到 window 上的外点击监听器,避免点触发按钮时立刻收起
            e.stopPropagation()
          }}
        >
          <ChatContextCapsule onClick={() => openParamsPanel('context')} />
          <button
            aria-label="对话参数"
            className={cn(
              'inline-flex h-6 items-center justify-center rounded-full border border-line-hairline bg-fill-faint px-1.5 text-body transition hover:border-line-standard hover:bg-fill-hover hover:text-strong cursor-pointer',
              paramsPanelOpen && paramsPanelTab === 'params' && 'bg-fill-hover text-accent border-line-standard',
              variant === 'living' && 'h-5 px-1'
            )}
            onClick={() => openParamsPanel('params')}
            title="对话与推理参数"
            type="button"
          >
            <SlidersHorizontal className="size-3.5" />
          </button>
        </div>
        {paramsPanelOpen && (
          <div
            className="absolute right-3 top-11 z-50 animate-in fade-in slide-in-from-top-2 duration-150"
            ref={paramsPanelRef}
          >
            <ChatParamsPanel defaultTab={paramsPanelTab} onClose={() => setParamsPanelOpen(false)} />
          </div>
        )}
      </div>
      <ChatContextAmbientLine />
      <ConversationSurface
        className={cn('flex-1 min-h-0 overflow-y-auto', surfaceClassName)}
        scrollRef={scrollRef}
        variant={variant}
      />
      <div className={cn('mt-auto shrink-0', inputWrapperClassName)}>
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
