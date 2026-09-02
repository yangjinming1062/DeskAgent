import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { $expressions } from '@/companion/3d/model-store'
import { useGatewayRequest } from '@/companion/boot/use-gateway-request'
import {
  $chatDraftFromUndo,
  $chatMessageList,
  $chatSessionId,
  $chatSessionKind,
  $chatStreamingTick,
  $chatTurnInFlight,
  $lastAssistantStreaming,
  $pendingExternalAttachment,
  $pendingPromptBatch,
  clearExternalAttachment,
  markAssistantTerminal,
  pushExternalAttachment
} from '@/companion/chat-store'
import { $spriteEmotion, $spriteState, setSpriteState } from '@/companion/companion-store'
import {
  $expressionAvatar,
  clearExpressionAvatar,
  requestExpressionAvatar
} from '@/companion/expression-avatar/expression-avatar-store'
import { useInteractiveRegion } from '@/companion/interactive-regions'
import { RESIZE_HANDLES } from '@/companion/panel/floating-panel'
import { $portraitUrl } from '@/companion/portrait-store'
import { $viewport } from '@/companion/spatial'
import { resolveDroppedFiles } from '@/shared/lib/file-drop'
import {
  ArrowRight,
  FileText,
  FolderOpen,
  ImageIcon,
  Mic,
  PanelLeft,
  Plus,
  Slash,
  Sparkles,
  SquareFilled,
  Video,
  X
} from '@/shared/lib/icons'
import { fuzzyFilterCommands } from '@/shared/lib/slash-commands'
import type { SlashCommandMeta } from '@/shared/lib/slash-commands'
import { cn } from '@/shared/lib/utils'
import { BorderBeam, BTN_ICON, HudCorners } from '@/shared/panel'
import { $gatewayState } from '@/shared/store/gateway'

import { MessageBubble } from './chat-dock-message-bubble'
import {
  attachVideoFile,
  IMAGE_EXT,
  pickFile,
  pickFolder,
  pickImage,
  pickVideo,
  VIDEO_EXT
} from './chat/chat-attach-picker'
import { EMOTION_MAP } from './chat/chat-mood-labels'
import { ChatParamsPanel } from './chat/chat-params-panel'
import { basename } from './chat/chat-path'
import { PendingAttachmentView } from './chat/chat-pending-attachment'
import { executeSlashCommand, slashPreCheck } from './chat/chat-slash'
import { ContextProgressBar } from './chat/context-progress-bar'
import { SessionDrawer } from './chat/session-drawer'
import { SlashCommandPopover } from './chat/slash-command-popover'
import { useChatSubmit } from './chat/use-chat-submit'
import { useSlashPopoverKeyboard } from './chat/use-slash-popover-keyboard'
import { usePanelDrag } from './hooks/use-panel-drag'
import { usePanelResize } from './hooks/use-panel-resize'
import { useVoiceRecorder } from './hooks/use-voice-recorder'
import {
  $currentSessionKind,
  $currentSessionTitle,
  $sessionListOpen,
  openMainSession,
  setSessionListOpen,
  switchSession
} from './session-list-store'

const DOCK_DEFAULT_WIDTH = 760
const DOCK_DEFAULT_HEIGHT = 540
const DOCK_MIN_WIDTH = 580
const DOCK_MIN_HEIGHT = 420
const DOCK_MAX_WIDTH = 1400
const DOCK_MAX_HEIGHT = 900

// DESIGN §2.1「用户输入起止」→ listening；停止输入该窗口后回落。
const TYPING_IDLE_MS = 2500

interface ChatDockProps {
  onClose: () => void
}

// 独立订阅 $chatStreamingTick：流式输出期间触发滚动跟随，避免 ChatDock 容器重渲染。
function ChatScrollAutoFollow({ scrollRef }: { scrollRef: React.RefObject<HTMLDivElement | null> }): null {
  const tick = useStore($chatStreamingTick)

  useEffect(() => {
    const el = scrollRef.current

    if (!el) {
      return
    }

    el.scrollTo?.({ top: el.scrollHeight, behavior: 'smooth' })
  }, [tick, scrollRef])

  return null
}

export function ChatDock({ onClose }: ChatDockProps): React.ReactElement {
  const list = useStore($chatMessageList)
  const lastAssistantStreaming = useStore($lastAssistantStreaming)
  const chatTurnInFlight = useStore($chatTurnInFlight)
  const pendingPromptBatch = useStore($pendingPromptBatch)
  const gatewayState = useStore($gatewayState)
  const portraitUrl = useStore($portraitUrl)
  const spriteEmotion = useStore($spriteEmotion)
  const spriteState = useStore($spriteState)
  const expressionAvatar = useStore($expressionAvatar)
  const customExpressions = useStore($expressions)
  const sessionListOpen = useStore($sessionListOpen)
  const currentSessionTitle = useStore($currentSessionTitle)
  const viewport = useStore($viewport)
  const { requestGateway } = useGatewayRequest()
  const [attachMenuOpen, setAttachMenuOpen] = useState(false)
  const attachMenuRef = useRef<HTMLDivElement>(null)
  const externalPathsRef = useRef<string[]>([])

  // IM 桥接会话在桌面端只读查看。
  const chatSessionKind = useStore($chatSessionKind)
  const currentSessionKind = useStore($currentSessionKind)

  const sessionKind = chatSessionKind || currentSessionKind || ''
  const isReadOnlySession = sessionKind === 'im'

  const submit = useChatSubmit({
    externalPathsRef,
    gatewayState,
    isReadOnlySession,
    onClearExternalPaths: () => clearExternalAttachment(),
    onPreCheckFail: msg => markAssistantTerminal({ error: msg })
  })

  const { text, setText, pending, setPending, sending, setSending, send, handleStop } = submit

  // Slash 命令自动补全弹层状态。
  // ``slashPopoverOpen`` 由 text 是否以 `/` 开头决定；``slashDismissed`` 标记用户按 Esc
  // 主动关闭——关闭后保留输入文本，再次输入自动重开。
  const [slashHighlightIndex, setSlashHighlightIndex] = useState(0)
  const [slashDismissed, setSlashDismissed] = useState(false)
  const slashTextMatches = text.trim().startsWith('/')
  const slashPopoverOpen = slashTextMatches && !slashDismissed

  const slashQuery = (() => {
    const trimmed = text.trim()

    if (!trimmed.startsWith('/')) {
      return ''
    }

    const body = trimmed.slice(1)
    const spaceIdx = body.search(/\s/)

    return spaceIdx === -1 ? body : body.slice(0, spaceIdx)
  })()

  const slashItems = slashPopoverOpen ? fuzzyFilterCommands(slashQuery, 8) : []

  useEffect(() => {
    setSlashDismissed(false)
    setSlashHighlightIndex(0)
  }, [text])

  const {
    recording,
    start: startRecording,
    stop: stopRecording
  } = useVoiceRecorder({
    isReadOnlySession
  })

  const scrollRef = useRef<HTMLDivElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useInteractiveRegion('chat-dock', panelRef)

  const { size, getResizeHandleProps } = usePanelResize({
    defaultSize: { height: DOCK_DEFAULT_HEIGHT, width: DOCK_DEFAULT_WIDTH },
    getPanel: () => panelRef.current,
    maxSize: { height: DOCK_MAX_HEIGHT, width: DOCK_MAX_WIDTH },
    minSize: { height: DOCK_MIN_HEIGHT, width: DOCK_MIN_WIDTH },
    offsetStorageKey: 'da.companion.chatDockOffset',
    sizeStorageKey: 'da.companion.chatDockSize'
  })

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  useEffect(() => {
    const unsubscribe = $chatDraftFromUndo.listen(value => {
      if (!value) {
        return
      }

      // 会话不匹配时清空总线而非保留——切到别会话再切回来时不会复活不属于本会话的 phantom 草稿。
      if (value.session_id !== $chatSessionId.get()) {
        $chatDraftFromUndo.set(null)

        return
      }

      setText(value.text)
      setPending(null)
      inputRef.current?.focus()
      $chatDraftFromUndo.set(null)
    })

    return () => {
      unsubscribe()
    }
  }, [setText, setPending])

  // 点击外部收起附件菜单
  useEffect(() => {
    if (!attachMenuOpen) {
      return
    }

    const handlePointerDown = (e: PointerEvent) => {
      if (attachMenuRef.current && !attachMenuRef.current.contains(e.target as Node)) {
        setAttachMenuOpen(false)
      }
    }

    window.addEventListener('pointerdown', handlePointerDown)

    return () => window.removeEventListener('pointerdown', handlePointerDown)
  }, [attachMenuOpen])

  // 文字输入走 listening（DESIGN §2.1 触发源 1）。优先级门控保证不打断
  // thinking / working / speaking 等更高状态；停止输入 TYPING_IDLE_MS 后回落。
  const typingIdleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const onTyping = (): void => {
    setSpriteState('listening')

    if (typingIdleTimerRef.current) {
      clearTimeout(typingIdleTimerRef.current)
    }

    typingIdleTimerRef.current = setTimeout(() => {
      typingIdleTimerRef.current = null

      if ($spriteState.get() === 'listening') {
        setSpriteState('idle', { force: true })
      }
    }, TYPING_IDLE_MS)
  }

  useEffect(
    () => () => {
      if (typingIdleTimerRef.current) {
        clearTimeout(typingIdleTimerRef.current)
      }
    },
    []
  )

  // 情绪激活时替换左栏头像，回退到半身像。
  useEffect(() => {
    if (spriteEmotion && spriteEmotion !== 'neutral') {
      void requestExpressionAvatar(spriteEmotion)
    } else {
      clearExpressionAvatar()
    }
  }, [spriteEmotion])

  useEffect(() => () => clearExpressionAvatar(), [])

  // Esc 优先收起会话抽屉或附件菜单，不打断正在输入的正文。
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape' && !e.defaultPrevented) {
        if (attachMenuOpen) {
          e.preventDefault()
          setAttachMenuOpen(false)

          return
        }

        if (sessionListOpen) {
          e.preventDefault()
          setSessionListOpen(false)
        }
      }
    }

    window.addEventListener('keydown', onKey)

    return () => window.removeEventListener('keydown', onKey)
  }, [sessionListOpen, attachMenuOpen])

  // 监听从 SpriteStage 投喂的外部文件（DESIGN §6.3「文件投喂」）：
  // 把首个媒体文件（图进缩略图槽、视频走上传）装进附件槽，其他路径暂存到 ref 留给 send() 一并提交。
  const pendingExternal = useStore($pendingExternalAttachment)

  useEffect(() => {
    if (!pendingExternal) {
      return
    }

    const mediaPaths: string[] = []
    const otherPaths: string[] = []

    for (const p of pendingExternal.paths) {
      ;(IMAGE_EXT.test(p) || VIDEO_EXT.test(p) ? mediaPaths : otherPaths).push(p)
    }

    if (mediaPaths.length > 0) {
      const first = mediaPaths[0]

      if (first && VIDEO_EXT.test(first)) {
        void attachVideoFile(first, setPending)
      } else if (first) {
        setPending({ type: 'image', value: first, fileName: basename(first) })
      }

      externalPathsRef.current = [...mediaPaths.slice(1), ...otherPaths]
    } else if (otherPaths.length === 1) {
      const first = otherPaths[0]
      const fileName = basename(first)
      setPending({ type: 'file', fileName, path: first })
      externalPathsRef.current = []
    } else {
      const names = otherPaths.map(basename).join('、')
      setText(t => (t ? `${t}\n${names}` : names))
      externalPathsRef.current = []
    }

    clearExternalAttachment()
  }, [pendingExternal, setPending, setText])

  // 切换会话即丢弃未发送附件：视频 URL 绑定上传时的会话，带到别的会话只会被跨会话校验拒绝。
  // 首次解析会话 id（attach/send 触发 ensureChatSession）不算切换，不清。
  const chatSessionId = useStore($chatSessionId)
  const prevSessionRef = useRef(chatSessionId)

  useEffect(() => {
    if (prevSessionRef.current !== chatSessionId) {
      prevSessionRef.current = chatSessionId
      setPending(null)
      externalPathsRef.current = []
    }
  }, [chatSessionId, setPending])

  // 面板挂载或网关就绪时，若当前消息列表为空，确保当前活跃会话（或主会话）的消息历史已加载
  const hydratedSessionRef = useRef<string | null>(null)

  useEffect(() => {
    if (gatewayState !== 'open') {
      return
    }

    const currentKey = chatSessionId ?? '__main__'

    if (list.length === 0 && hydratedSessionRef.current !== currentKey) {
      hydratedSessionRef.current = currentKey

      if (chatSessionId) {
        void switchSession(chatSessionId)
      } else {
        void openMainSession()
      }
    }
  }, [gatewayState, chatSessionId, list.length])

  useEffect(() => {
    scrollRef.current?.scrollTo?.({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [list])

  const onPaste = async (e: React.ClipboardEvent): Promise<void> => {
    const items = e.clipboardData?.items

    if (!items) {
      return
    }

    for (const item of items) {
      // 从资源管理器复制的视频文件：取真实路径走上传（图片位图粘贴走 saveClipboardImage 分支）。
      if (item.kind === 'file' && item.type.startsWith('video/')) {
        const file = item.getAsFile()
        const paths = file ? resolveDroppedFiles([file]) : []
        const path = paths[0]

        if (path) {
          e.preventDefault()
          void attachVideoFile(path, setPending)

          return
        }
      }

      if (item.type.startsWith('image/')) {
        e.preventDefault()

        try {
          const path = await window.spiritagent.saveClipboardImage()

          if (path) {
            setPending({ type: 'image', value: path, fileName: basename(path) })
          }
        } catch {
          /* 忽略剪贴板读取失败 */
        }

        return
      }
    }
  }

  // DESIGN §6.1「支持拖拽文件」：面板本体也是投喂入口——解析真实路径后走与
  // 精灵投喂同一条附件管线（首个媒体进附件槽、其余随 send() 一并提交）。
  const onDrop = (e: React.DragEvent): void => {
    const paths = resolveDroppedFiles(e.dataTransfer?.files)

    if (paths.length > 0) {
      e.preventDefault()
      clearExternalAttachment()
      pushExternalAttachment(paths)
    }
  }

  const onSlashSelect = async (cmd: SlashCommandMeta, args: string[]): Promise<void> => {
    if (cmd.name === 'remember' && args.length === 0) {
      setText(`/${cmd.name} `)
      inputRef.current?.focus()

      return
    }

    const preCheck = slashPreCheck(pending, sending)

    if (preCheck) {
      markAssistantTerminal({ error: preCheck })

      return
    }

    await executeSlashCommand(cmd, args, {
      onFinish: () => {
        setSending(false)
      },
      onStart: () => {
        setSending(true)
        setText('')
      },
      requestGateway
    })
  }

  const onSlashKeyDown = useSlashPopoverKeyboard({
    highlightedIndex: slashHighlightIndex,
    isOpen: slashPopoverOpen,
    items: slashItems,
    onDismiss: () => setSlashDismissed(true),
    onHighlightIndexChange: setSlashHighlightIndex,
    onSelect: (cmd, args) => {
      void onSlashSelect(cmd, args)
    },
    onSend: () => void send(),
    text
  })

  const isTurnPendingOrInFlight = pendingPromptBatch.length > 0 || chatTurnInFlight
  const showTyping = isTurnPendingOrInFlight && !lastAssistantStreaming && gatewayState === 'open'

  const isGenerating = gatewayState === 'open' && (isTurnPendingOrInFlight || lastAssistantStreaming)

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

      case 'working':
        return { label: '专注工作中', icon: '💼' }

      default:
        return { label: '平静温和', icon: '😊' }
    }
  }, [spriteEmotion, spriteState, customExpressions])

  return (
    <div className="fixed inset-0 z-40 pointer-events-none">
      <div
        className="relative flex flex-row overflow-hidden rounded-2xl border border-line-strong bg-surface-panel text-strong shadow-2xl border-beam-container"
        onDragOver={e => {
          if (e.dataTransfer.types.includes('Files')) {
            e.preventDefault()
          }
        }}
        onDrop={onDrop}
        ref={panelRef}
        style={{
          height: `min(calc(100vh - 2rem), ${currentH}px)`,
          left: baseLeft,
          pointerEvents: 'auto',
          position: 'fixed',
          top: baseTop,
          transform: storedOffset ? `translate3d(${storedOffset.dx}px, ${storedOffset.dy}px, 0)` : undefined,
          width: `min(calc(100vw - 2rem), ${currentW}px)`
        }}
      >
        <BorderBeam fast={spriteState === 'thinking' || spriteState === 'speaking'} />
        <HudCorners size={8} />

        {/* Resize Handles (8 Directions) */}
        {RESIZE_HANDLES.map(h => (
          <div aria-hidden="true" className={h.className} key={h.dir} {...getResizeHandleProps(h.dir)} />
        ))}

        {sessionListOpen && <SessionDrawer onClose={() => setSessionListOpen(false)} />}

        {/* Left Column: Visual Anchor, Character Emotion & Session Parameters (Draggable) */}
        <div
          className="flex w-52 flex-shrink-0 cursor-grab flex-col items-center justify-between border-r border-line-standard bg-surface-chrome p-3 select-none active:cursor-grabbing"
          title="拖动以移动对话框"
          {...dragBind}
        >
          <div className="flex flex-col items-center w-full min-h-0 overflow-y-auto no-scrollbar">
            <div className="relative group mt-0.5">
              <div className="relative h-28 w-28 overflow-hidden rounded-2xl border border-line-standard bg-fill-faint shadow-xl transition duration-300 group-hover:border-line-strong">
                {(expressionAvatar?.dataUrl ?? portraitUrl) ? (
                  <img
                    alt="角色形象"
                    className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
                    src={expressionAvatar?.dataUrl ?? portraitUrl ?? undefined}
                  />
                ) : (
                  <div className="flex h-full w-full flex-col items-center justify-center bg-linear-to-b from-white/10 to-white/5 p-4 text-center">
                    <Sparkles className="size-7 animate-pulse text-faint" />
                    <span className="mt-2 text-[11px] text-faint">伙伴形象</span>
                  </div>
                )}
              </div>
              {/* Status Badge floating at bottom of avatar */}
              <div className="absolute -bottom-2.5 left-1/2 -translate-x-1/2 flex items-center gap-1.5 rounded-full border border-line-standard bg-surface-panel px-2.5 py-0.5 text-[10px] text-strong shadow-md whitespace-nowrap">
                <span
                  className={`h-1.5 w-1.5 rounded-full ${
                    spriteState === 'thinking'
                      ? 'bg-amber-400 animate-ping'
                      : spriteState === 'speaking'
                        ? 'bg-blue-400 animate-pulse'
                        : spriteState === 'listening'
                          ? 'bg-rose-400 animate-pulse'
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
                        : '在线陪伴'}
                </span>
              </div>
            </div>

            <div className="mt-4 flex flex-col items-center text-center w-full px-1">
              <div className="flex items-center gap-1.5 rounded-full border border-line-standard bg-fill-faint px-2.5 py-0.5 text-xs text-strong shadow-sm">
                <span className="text-sm">{currentMood.icon}</span>
                <span className="font-medium tracking-wide text-[11px]">{currentMood.label}</span>
              </div>
            </div>
          </div>

          <div className="w-full flex flex-col items-center gap-2 pt-2 border-t border-line-hairline shrink-0">
            <ChatParamsPanel />
            <p className="text-[10px] text-faint pt-0.5">{gatewayState === 'open' ? '随时倾听中' : '网络连接中…'}</p>
          </div>
        </div>

        {/* Right Column: Chat Stream & Input */}
        <div className="flex flex-1 flex-col min-w-0 bg-surface-panel">
          <div
            className="flex cursor-grab items-center justify-between gap-2 border-b border-line-standard px-3 py-2 active:cursor-grabbing"
            title="拖动以移动对话框"
            {...dragBind}
          >
            <div className="flex min-w-0 items-center gap-1.5">
              <button
                aria-label="切换对话"
                className={cn(BTN_ICON, sessionListOpen && 'bg-fill-hover text-strong')}
                onClick={() => setSessionListOpen(!sessionListOpen)}
                title="切换历史对话"
                type="button"
              >
                <PanelLeft />
              </button>
              <span className="truncate text-sm font-medium text-strong">{currentSessionTitle}</span>
              <span className="hidden font-mono text-[9px] text-faint uppercase tracking-widest sm:inline-block">
                [CONSOLE: ONLINE]
              </span>
            </div>
            <div className="flex items-center gap-0.5">
              <button
                aria-label="关闭对话"
                className={cn(
                  BTN_ICON,
                  'hover:border hover:border-line-strong hover:bg-rose-500/15 hover:text-rose-300'
                )}
                onClick={onClose}
                type="button"
              >
                <X />
              </button>
            </div>
          </div>

          <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4" ref={scrollRef}>
            {list.length === 0 && !sending && (
              <p className="mt-8 text-center text-sm text-faint">说点什么，或发送文件/图片/视频给我看看～</p>
            )}
            {list.map(item => (
              <MessageBubble key={item.id} message={item} />
            ))}
            {showTyping && (
              <div className="flex justify-start">
                <span className="flex items-center gap-1 rounded-2xl rounded-bl-sm border border-line-hairline bg-surface-card px-3.5 py-2.5">
                  <i className="size-1.5 animate-bounce rounded-full bg-fill-faint [animation-delay:0ms]" />
                  <i className="size-1.5 animate-bounce rounded-full bg-fill-faint [animation-delay:150ms]" />
                  <i className="size-1.5 animate-bounce rounded-full bg-fill-faint [animation-delay:300ms]" />
                </span>
              </div>
            )}
            <ChatScrollAutoFollow scrollRef={scrollRef} />
          </div>

          <div className="border-t border-line-standard p-3 pt-2.5 flex flex-col gap-1.5">
            {gatewayState !== 'open' && <p className="mb-1 text-center text-xs text-amber-300/70">正在连接…</p>}
            {isReadOnlySession && <p className="mb-1 text-center text-xs text-faint">IM 对话 · 只读</p>}

            <div className="relative flex flex-col gap-2 rounded-xl border border-line-standard bg-fill-faint p-2.5 transition focus-within:border-accent/60 focus-within:bg-fill-hover shadow-sm">
              <div className="relative w-full">
                <textarea
                  className="max-h-32 min-h-[42px] w-full resize-none border-0 bg-transparent p-0 text-sm leading-relaxed text-strong outline-none placeholder:text-faint disabled:pointer-events-none disabled:opacity-40"
                  disabled={isReadOnlySession}
                  onChange={e => {
                    setText(e.target.value)
                    onTyping()
                  }}
                  onKeyDown={onSlashKeyDown}
                  onPaste={onPaste}
                  placeholder="输入消息，Enter 发送，Shift+Enter 换行；输入 / 触发命令"
                  ref={inputRef}
                  rows={1}
                  value={text}
                />

                {slashPopoverOpen && slashItems.length > 0 && (
                  <SlashCommandPopover
                    highlightedIndex={slashHighlightIndex}
                    onHighlight={setSlashHighlightIndex}
                    onSelect={cmd => {
                      void onSlashSelect(cmd, [])
                    }}
                    query={slashQuery}
                  />
                )}
              </div>

              {pending && (
                <PendingAttachmentView
                  onRemove={() => setPending(null)}
                  onRetry={pending.type === 'video' ? () => void attachVideoFile(pending.path, setPending) : undefined}
                  pending={pending}
                  sending={sending}
                />
              )}

              <div className="flex items-center justify-between gap-2 pt-1 border-t border-line-hairline">
                <div className="flex items-center gap-1 shrink-0">
                  <div className="relative" ref={attachMenuRef}>
                    <button
                      aria-label="添加附件"
                      className={cn(
                        'inline-flex size-7 items-center justify-center rounded-lg text-muted transition hover:bg-fill-hover hover:text-strong disabled:pointer-events-none disabled:opacity-40',
                        attachMenuOpen && 'bg-fill-hover text-strong'
                      )}
                      disabled={isReadOnlySession}
                      onClick={() => setAttachMenuOpen(!attachMenuOpen)}
                      title="添加附件（文件、文件夹、图片、视频）"
                      type="button"
                    >
                      <Plus className="size-4" />
                    </button>

                    {attachMenuOpen && (
                      <div className="absolute bottom-full mb-2 left-0 z-50 flex w-36 flex-col gap-0.5 rounded-xl border border-line-standard bg-surface-card p-1 shadow-2xl backdrop-blur-md animate-in fade-in zoom-in-95 duration-150">
                        <button
                          className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs text-body transition hover:bg-fill-hover hover:text-strong text-left"
                          onClick={() => void pickFile(setPending)}
                          type="button"
                        >
                          <FileText className="size-3.5 text-accent" />
                          <span>添加文件</span>
                        </button>
                        <button
                          className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs text-body transition hover:bg-fill-hover hover:text-strong text-left"
                          onClick={() => void pickFolder(setPending)}
                          type="button"
                        >
                          <FolderOpen className="size-3.5 text-amber-400" />
                          <span>添加文件夹</span>
                        </button>
                        <button
                          className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs text-body transition hover:bg-fill-hover hover:text-strong text-left"
                          onClick={() => void pickImage(setPending)}
                          type="button"
                        >
                          <ImageIcon className="size-3.5 text-emerald-400" />
                          <span>添加图片</span>
                        </button>
                        <button
                          className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs text-body transition hover:bg-fill-hover hover:text-strong text-left"
                          onClick={() => void pickVideo(setPending)}
                          type="button"
                        >
                          <Video className="size-3.5 text-rose-400" />
                          <span>添加视频</span>
                        </button>
                      </div>
                    )}
                  </div>

                  <button
                    aria-label="快捷命令"
                    className="inline-flex size-7 items-center justify-center rounded-lg text-muted transition hover:bg-fill-hover hover:text-strong disabled:pointer-events-none disabled:opacity-40"
                    disabled={isReadOnlySession}
                    onClick={() => {
                      if (!text.startsWith('/')) {
                        setText(t => (t ? `/${t}` : '/'))
                      }

                      setSlashDismissed(false)
                      inputRef.current?.focus()
                    }}
                    title="快捷命令 (/)"
                    type="button"
                  >
                    <Slash className="size-3.5" />
                  </button>
                </div>

                <div className="flex-1 min-w-0 px-2 flex items-center">
                  <ContextProgressBar />
                </div>

                <div className="flex items-center gap-1.5 shrink-0">
                  <button
                    className={cn(
                      'inline-flex size-7 items-center justify-center rounded-full transition disabled:pointer-events-none disabled:opacity-40',
                      recording
                        ? 'border border-rose-400/70 bg-rose-500/25 text-rose-200 animate-pulse'
                        : 'text-muted hover:bg-fill-hover hover:text-strong'
                    )}
                    disabled={isReadOnlySession}
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
                    title={recording ? '松开发送语音' : '按住录制语音消息'}
                    type="button"
                  >
                    <Mic className="size-3.5" />
                  </button>

                  <button
                    aria-label={isGenerating ? '停止生成' : '发送消息'}
                    className={cn(
                      'inline-flex size-7 items-center justify-center rounded-full transition disabled:pointer-events-none disabled:opacity-30',
                      isGenerating
                        ? 'bg-rose-500/90 hover:bg-rose-600 text-on-accent shadow-xs'
                        : 'bg-accent hover:bg-accent/85 text-on-accent shadow-xs'
                    )}
                    disabled={
                      isReadOnlySession ||
                      (!isGenerating &&
                        (sending ||
                          gatewayState !== 'open' ||
                          (!text.trim() && !pending) ||
                          (pending?.type === 'video' && pending.status !== 'ready')))
                    }
                    onClick={() => void (isGenerating ? handleStop() : send())}
                    title={isGenerating ? '停止生成' : '发送消息 (Enter)'}
                    type="button"
                  >
                    {isGenerating ? <SquareFilled className="size-3" /> : <ArrowRight className="size-3.5" />}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
