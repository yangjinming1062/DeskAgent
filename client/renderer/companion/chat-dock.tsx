import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { $expressions } from '@/companion/3d/model-store'
import { useGatewayRequest } from '@/companion/boot/use-gateway-request'
import {
  $chatMessages,
  $chatSessionId,
  $chatTurnInFlight,
  cancelPendingFlush,
  clearPendingPrompts,
  finalizeAssistantMessage,
  pushPendingPrompt,
  pushUserMessage,
  schedulePendingFlush,
  setAssistantCancelled,
  setAssistantError
} from '@/companion/chat-store'
import { $spriteEmotion, $spriteState, setSpriteState } from '@/companion/companion-store'
import {
  $expressionAvatar,
  clearExpressionAvatar,
  requestExpressionAvatar
} from '@/companion/expression-avatar/expression-avatar-store'
import { useInteractiveRegion } from '@/companion/interactive-regions'
import { $portraitUrl } from '@/companion/portrait-store'
import { $viewport } from '@/companion/spatial'
import { $gatewayState } from '@/shared/store/gateway'

import { MessageBubble } from './chat-dock-message-bubble'
import { usePanelDrag } from './hooks/use-panel-drag'
import { type ResizeDirection, usePanelResize } from './hooks/use-panel-resize'
import { useVoiceRecorder } from './hooks/use-voice-recorder'
import { SessionListPanel } from './session-list'
import { $sessionListOpen, openMainSession, setSessionListOpen } from './session-list-store'

const DOCK_DEFAULT_WIDTH = 760
const DOCK_DEFAULT_HEIGHT = 540
const DOCK_MIN_WIDTH = 580
const DOCK_MIN_HEIGHT = 420
const DOCK_MAX_WIDTH = 1400
const DOCK_MAX_HEIGHT = 900

const EMOTION_MAP: Record<string, { label: string; icon: string }> = {
  happy: { label: '开心愉悦', icon: '😊' },
  excited: { label: '兴奋雀跃', icon: '✨' },
  curious: { label: '充满好奇', icon: '🧐' },
  grateful: { label: '心怀感激', icon: '💖' },
  playful: { label: '顽皮逗趣', icon: '😜' },
  proud: { label: '自信自豪', icon: '🌟' },
  smug: { label: '有点得意', icon: '😏' },
  shy: { label: '害羞腼腆', icon: '😳' },
  relieved: { label: '安心放松', icon: '😌' },
  concerned: { label: '关切担忧', icon: '🥺' },
  confused: { label: '略带疑惑', icon: '🤔' },
  surprised: { label: '感到惊讶', icon: '😮' },
  embarrassed: { label: '不好意思', icon: '😅' },
  apologetic: { label: '抱歉内疚', icon: '🙇' },
  sad: { label: '有些低落', icon: '😢' },
  lonely: { label: '稍感孤单', icon: '🌧️' },
  bored: { label: '百无聊赖', icon: '🥱' },
  sleepy: { label: '昏昏欲睡', icon: '😴' },
  pout: { label: '气鼓鼓中', icon: '😤' },
  angry: { label: '有些生气', icon: '💢' },
  scared: { label: '受到惊吓', icon: '😨' }
}

const RESIZE_HANDLES: Array<{
  dir: ResizeDirection
  className: string
}> = [
  { dir: 'n', className: 'absolute -top-1 left-3 right-3 h-2.5 cursor-ns-resize z-20 touch-none' },
  { dir: 's', className: 'absolute -bottom-1 left-3 right-3 h-2.5 cursor-ns-resize z-20 touch-none' },
  { dir: 'w', className: 'absolute -left-1 top-3 bottom-3 w-2.5 cursor-ew-resize z-20 touch-none' },
  { dir: 'e', className: 'absolute -right-1 top-3 bottom-3 w-2.5 cursor-ew-resize z-20 touch-none' },
  { dir: 'nw', className: 'absolute -top-1.5 -left-1.5 h-4 w-4 cursor-nwse-resize z-30 touch-none' },
  { dir: 'ne', className: 'absolute -top-1.5 -right-1.5 h-4 w-4 cursor-nesw-resize z-30 touch-none' },
  { dir: 'sw', className: 'absolute -bottom-1.5 -left-1.5 h-4 w-4 cursor-nesw-resize z-30 touch-none' },
  { dir: 'se', className: 'absolute -bottom-1.5 -right-1.5 h-4 w-4 cursor-nwse-resize z-30 touch-none' }
]

interface ChatDockProps {
  onClose: () => void
  onOpenVoiceCall?: () => void
}

export function ChatDock({ onClose, onOpenVoiceCall }: ChatDockProps): React.ReactElement {
  const messages = useStore($chatMessages)
  const gatewayState = useStore($gatewayState)
  const portraitUrl = useStore($portraitUrl)
  const spriteEmotion = useStore($spriteEmotion)
  const spriteState = useStore($spriteState)
  const expressionAvatar = useStore($expressionAvatar)
  const customExpressions = useStore($expressions)
  const sessionListOpen = useStore($sessionListOpen)
  const viewport = useStore($viewport)
  const { requestGateway } = useGatewayRequest()
  const [text, setText] = useState('')
  const [pendingImage, setPendingImage] = useState<string | null>(null)
  const [sending, setSending] = useState(false)

  const {
    recording,
    start: startRecording,
    stop: stopRecording
  } = useVoiceRecorder({
    requestGateway,
    onTranscribed: text => {
      pushUserMessage(text)
    }
  })

  const scrollRef = useRef<HTMLDivElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useInteractiveRegion('chat-dock', panelRef)

  const { size, getResizeHandleProps } = usePanelResize({
    sizeStorageKey: 'da.companion.chatDockSize',
    offsetStorageKey: 'da.companion.chatDockOffset',
    defaultSize: { width: DOCK_DEFAULT_WIDTH, height: DOCK_DEFAULT_HEIGHT },
    minSize: { width: DOCK_MIN_WIDTH, height: DOCK_MIN_HEIGHT },
    maxSize: { width: DOCK_MAX_WIDTH, height: DOCK_MAX_HEIGHT },
    getPanel: () => panelRef.current
  })

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  // 表情脸：在 affect 激活时替换左栏头像；
  // 没有情绪或图片未就绪时回退到半身像。
  // 订阅放在这里（而不是 store 里），是为了让仅桌面端的表情
  // 在聊天窗口关闭时不会触发生成。
  useEffect(() => {
    if (spriteEmotion && spriteEmotion !== 'neutral') {
      void requestExpressionAvatar(spriteEmotion)
    } else {
      clearExpressionAvatar()
    }
  }, [spriteEmotion])

  useEffect(() => () => clearExpressionAvatar(), [])

  useEffect(() => {
    scrollRef.current?.scrollTo?.({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

  const ensureSession = async (): Promise<string> => {
    const existing = $chatSessionId.get()

    if (existing) {
      return existing
    }

    const sessionId = await openMainSession()

    if (!sessionId) {
      throw new Error('无法打开日常对话')
    }

    return sessionId
  }

  const onPaste = async (e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items

    if (!items) {
      return
    }

    for (const item of items) {
      if (item.type.startsWith('image/')) {
        e.preventDefault()

        try {
          const path = await window.spiritagent.saveClipboardImage()

          if (path) {
            setPendingImage(path)
          }
        } catch {
          /* ignore clipboard read failure */
        }

        return
      }
    }
  }

  const send = async () => {
    const trimmed = text.trim()

    if ((!trimmed && !pendingImage) || sending) {
      return
    }

    if (gatewayState !== 'open') {
      return
    }

    setSending(true)

    try {
      const id = await ensureSession()
      let fullText = trimmed
      const attachments: string[] = []

      if (pendingImage) {
        let attachmentUrl = pendingImage

        try {
          if (!pendingImage.startsWith('data:')) {
            const dataUrl = await window.spiritagent.readFileDataUrl(pendingImage)

            if (dataUrl) {
              attachmentUrl = dataUrl
            }
          }
        } catch {
          /* keep the local path */
        }

        const ref = await requestGateway<{ ref_text?: string }>('image.attach', {
          session_id: id,
          path: attachmentUrl
        })

        if (ref.ref_text) {
          fullText = `${fullText}\n${ref.ref_text}`.trim()
          attachments.push(pendingImage)
        }
      }

      pushUserMessage(fullText || '（图片）', attachments.length ? attachments : undefined)
      setText('')
      setPendingImage(null)
      setSpriteState('thinking')

      pushPendingPrompt({
        text: fullText || '请看这张图',
        attachments: attachments.length ? attachments : undefined
      })
      schedulePendingFlush()
    } catch (err) {
      setAssistantError(err instanceof Error ? err.message : '发送失败')
      setSpriteState('idle')
      setPendingImage(null)
    } finally {
      setSending(false)
    }
  }

  const lastIsUser = messages[messages.length - 1]?.role === 'user'
  const showTyping = lastIsUser && gatewayState === 'open'
  const lastMsg = messages[messages.length - 1]

  const isGenerating =
    gatewayState === 'open' && (showTyping || (lastMsg?.role === 'assistant' && lastMsg.streaming === true))

  const handleStop = async () => {
    cancelPendingFlush()
    clearPendingPrompts()
    $chatTurnInFlight.set(false)
    const sid = $chatSessionId.get()

    if (sid) {
      try {
        await requestGateway('session.interrupt', { session_id: sid })
      } catch {
        /* best effort */
      }
    }

    void window.spiritagent?.runnerCancel?.().catch(() => {})

    const last = $chatMessages.get().at(-1)

    if (last?.role === 'assistant' && last.streaming) {
      finalizeAssistantMessage()
    } else {
      setAssistantCancelled()
    }

    setSpriteState('idle', { force: true })
  }

  const currentW = size.width
  const currentH = size.height

  const baseLeft = Math.max(16, Math.round((viewport.width - Math.min(viewport.width - 32, currentW)) / 2))
  const baseTop = Math.max(16, Math.round((viewport.height - Math.min(viewport.height - 32, currentH)) / 2))

  const { bind: dragBind, storedOffset } = usePanelDrag('da.companion.chatDockOffset', () => panelRef.current)

  const currentMood = useMemo(() => {
    if (spriteEmotion) {
      // 自定义情绪注册表（create_expression）：label + 可选 icon，
      // 尚未水合的 token 用通用渲染。
      const custom = customExpressions.find(e => e.name === spriteEmotion)

      return EMOTION_MAP[spriteEmotion] ?? { label: custom?.label || spriteEmotion, icon: custom?.icon || '💫' }
    }

    switch (spriteState) {
      case 'thinking':
        return { label: '正在思考', icon: '💭' }

      case 'speaking':
        return { label: '正在回复', icon: '💬' }

      case 'listening':
        return { label: '专注倾听', icon: '🎙️' }

      case 'sleeping':
        return { label: '休息小憩', icon: '🌙' }

      case 'working':
        return { label: '专注工作中', icon: '💼' }

      default:
        return { label: '平静温和', icon: '😊' }
    }
  }, [spriteEmotion, spriteState, customExpressions])

  return (
    <div className="fixed inset-0 z-40 pointer-events-none">
      <div
        className="relative flex flex-row overflow-hidden rounded-2xl border border-white/10 bg-[#18181b] text-white shadow-2xl"
        ref={panelRef}
        style={{
          position: 'fixed',
          left: baseLeft,
          top: baseTop,
          width: `min(calc(100vw - 2rem), ${currentW}px)`,
          height: `min(calc(100vh - 2rem), ${currentH}px)`,
          pointerEvents: 'auto',
          transform: storedOffset ? `translate3d(${storedOffset.dx}px, ${storedOffset.dy}px, 0)` : undefined
        }}
      >
        {/* Resize Handles (8 Directions) */}
        {RESIZE_HANDLES.map(h => (
          <div aria-hidden="true" className={h.className} key={h.dir} {...getResizeHandleProps(h.dir)} />
        ))}

        {/* Left Column: Visual Anchor & Character Emotion Status (Draggable) */}
        <div
          className="flex w-52 flex-shrink-0 cursor-grab flex-col items-center justify-between border-r border-white/10 bg-[#131316] p-4 select-none active:cursor-grabbing"
          {...dragBind}
          title="拖动以移动对话框"
        >
          <div className="flex flex-col items-center w-full">
            {/* Character Avatar with subtle glow and framing */}
            <div className="relative group mt-1">
              <div className="relative h-36 w-36 overflow-hidden rounded-2xl border border-white/15 bg-white/5 shadow-xl transition duration-300 group-hover:border-white/30">
                {(expressionAvatar?.dataUrl ?? portraitUrl) ? (
                  <img
                    alt="角色形象"
                    className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
                    src={expressionAvatar?.dataUrl ?? portraitUrl ?? undefined}
                  />
                ) : (
                  <div className="flex h-full w-full flex-col items-center justify-center bg-linear-to-b from-white/10 to-white/5 p-4 text-center">
                    <span className="text-3xl animate-pulse">✨</span>
                    <span className="mt-2 text-[11px] text-white/40">伙伴形象</span>
                  </div>
                )}
              </div>
              {/* Status Badge floating at bottom of avatar */}
              <div className="absolute -bottom-2.5 left-1/2 -translate-x-1/2 flex items-center gap-1.5 rounded-full border border-white/15 bg-[#18181b] px-2.5 py-0.5 text-[10px] text-white/90 shadow-md whitespace-nowrap">
                <span
                  className={`h-1.5 w-1.5 rounded-full ${
                    spriteState === 'thinking'
                      ? 'bg-amber-400 animate-ping'
                      : spriteState === 'speaking'
                        ? 'bg-blue-400 animate-pulse'
                        : spriteState === 'listening'
                          ? 'bg-rose-400 animate-pulse'
                          : spriteState === 'sleeping'
                            ? 'bg-purple-400'
                            : 'bg-emerald-400'
                  }`}
                />
                <span>
                  {spriteState === 'thinking'
                    ? '思考中…'
                    : spriteState === 'speaking'
                      ? '回复中…'
                      : spriteState === 'listening'
                        ? '聆听中…'
                        : spriteState === 'sleeping'
                          ? '小憩中'
                          : '在线陪伴'}
                </span>
              </div>
            </div>

            {/* Current Emotion Status Display */}
            <div className="mt-6 flex flex-col items-center text-center w-full px-2">
              <div className="flex items-center gap-1.5 rounded-full border border-white/15 bg-white/5 px-3 py-1 text-xs text-white/90 shadow-sm">
                <span className="text-sm">{currentMood.icon}</span>
                <span className="font-medium tracking-wide">{currentMood.label}</span>
              </div>
              <span className="mt-2 text-[10px] text-white/40 tracking-wider">当前情绪状态</span>
            </div>
          </div>

          {/* Quick status summary / hint at bottom */}
          <div className="w-full pt-3 text-center border-t border-white/5">
            <p className="text-[10px] text-white/35">{gatewayState === 'open' ? '随时倾听中' : '网络连接中…'}</p>
          </div>
        </div>

        {/* Right Column: Chat Stream & Input */}
        <div className="flex flex-1 flex-col min-w-0 bg-[#18181b]">
          {/* Header Bar */}
          <div
            className="flex cursor-grab items-center justify-between gap-2 border-b border-white/10 px-3.5 py-2.5 active:cursor-grabbing"
            {...dragBind}
            title="拖动以移动对话框"
          >
            <div className="flex items-center gap-2">
              <button
                className="rounded-full border border-white/20 bg-white/10 px-3 py-1 text-[11px] text-white/80 transition hover:bg-white/20 hover:text-white"
                onClick={() => setSessionListOpen(true)}
                title="切换历史对话"
                type="button"
              >
                💬 切换对话
              </button>
            </div>
            <button
              aria-label="关闭对话"
              className="text-white/50 transition hover:text-white px-1.5 py-0.5 rounded-md hover:bg-white/10"
              onClick={onClose}
              type="button"
            >
              ✕
            </button>
          </div>

          {/* Messages Area */}
          <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4" ref={scrollRef}>
            {messages.length === 0 && !sending && (
              <p className="mt-8 text-center text-sm text-white/40">说点什么，或粘贴一张图给我看看～</p>
            )}
            {messages.map(m => (
              <MessageBubble key={m.id} message={m} />
            ))}
            {showTyping && (
              <div className="flex justify-start">
                <span className="rounded-2xl rounded-bl-sm bg-white/10 px-3 py-2 text-sm text-white/60">…</span>
              </div>
            )}
          </div>

          {pendingImage && (
            <div className="border-t border-white/10 px-4 py-2 text-xs text-white/60">
              📎 已附加图片 {sending ? '（发送中…）' : ''}
            </div>
          )}

          {/* Input Area */}
          <div className="border-t border-white/10 p-3 bg-[#18181b]">
            {gatewayState !== 'open' && <p className="mb-2 text-center text-xs text-amber-300/70">正在连接…</p>}
            <div className="flex items-end gap-2">
              <textarea
                className="max-h-32 min-h-[38px] flex-1 resize-none rounded-lg border border-white/15 bg-white/5 px-3 py-2 text-sm leading-normal text-white outline-none placeholder:text-white/40 focus:border-white/40"
                onChange={e => setText(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    void send()
                  }
                }}
                onPaste={onPaste}
                placeholder="输入消息，Enter 发送，Shift+Enter 换行"
                ref={inputRef}
                rows={1}
                value={text}
              />
              <button
                className={`shrink-0 whitespace-nowrap rounded-lg border px-3 py-2 text-xs font-medium transition ${
                  recording
                    ? 'border-red-400/80 bg-red-500/30 text-white animate-pulse'
                    : 'border-white/20 bg-white/5 text-white/70 hover:bg-white/15 hover:text-white'
                }`}
                onMouseDown={() => void startRecording()}
                onMouseLeave={() => {
                  if (recording) {
                    void stopRecording()
                  }
                }}
                onMouseUp={() => void stopRecording()}
                onPointerCancel={() => void stopRecording()}
                onPointerLeave={() => {
                  if (recording) {
                    void stopRecording()
                  }
                }}
                onTouchEnd={() => void stopRecording()}
                onTouchStart={() => void startRecording()}
                title="按住录制语音消息"
                type="button"
              >
                {recording ? '松开发送' : '🎤 语音'}
              </button>
              {onOpenVoiceCall && (
                <button
                  className="shrink-0 whitespace-nowrap rounded-lg border border-white/20 bg-white/5 px-3 py-2 text-xs font-medium text-white/75 transition hover:bg-white/15 hover:text-white"
                  onClick={onOpenVoiceCall}
                  title="开启实时语音通话模式"
                  type="button"
                >
                  📞 通话
                </button>
              )}
              <button
                className="shrink-0 whitespace-nowrap rounded-lg bg-white/90 px-4 py-2 text-sm font-medium text-black transition hover:bg-white disabled:opacity-40"
                disabled={!isGenerating && (sending || gatewayState !== 'open' || (!text.trim() && !pendingImage))}
                onClick={() => void (isGenerating ? handleStop() : send())}
                type="button"
              >
                {isGenerating ? '停止' : '发送'}
              </button>
            </div>
          </div>
        </div>
      </div>
      {sessionListOpen && <SessionListPanel onClose={() => setSessionListOpen(false)} />}
    </div>
  )
}
