import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { $expressions } from '@/companion/3d/model-store'
import { cancelAutoVoice } from '@/companion/auto-voice-stream'
import { useGatewayRequest } from '@/companion/boot/use-gateway-request'
import {
  $chatMessageBodies,
  $chatMessageList,
  $chatSessionId,
  $chatStreamingTick,
  $chatTurnInFlight,
  $lastAssistantStreaming,
  $pendingExternalAttachment,
  cancelPendingFlush,
  clearExternalAttachment,
  finalizeAssistantMessage,
  pushExternalAttachment,
  pushPendingPrompt,
  pushUserMessage,
  schedulePendingFlush,
  setAssistantCancelled,
  setAssistantError,
  submitPendingBatch
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
import { $archivedSessions, $searchResults, $sessions } from '@/companion/session-list-store'
import { $viewport } from '@/companion/spatial'
import { Mic, PanelLeft, Paperclip, Phone, Sparkles, Video, X } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { BTN_ICON, BTN_PRIMARY } from '@/shared/panel'
import { $gatewayState } from '@/shared/store/gateway'
import type { ChatAttachment } from '@/shared/types/spiritagent'

import { MessageBubble } from './chat-dock-message-bubble'
import { useResolvedMediaSrc } from './chat-media-src'
import { SessionDrawer } from './chat/session-drawer'
import { usePanelDrag } from './hooks/use-panel-drag'
import { usePanelResize } from './hooks/use-panel-resize'
import { useVoiceRecorder } from './hooks/use-voice-recorder'
import { openMediaViewer } from './media-viewer-overlay'
import { $sessionListOpen, findSessionInfo, openMainSession, setSessionListOpen } from './session-list-store'

const DOCK_DEFAULT_WIDTH = 760
const DOCK_DEFAULT_HEIGHT = 540
const DOCK_MIN_WIDTH = 580
const DOCK_MIN_HEIGHT = 420
const DOCK_MAX_WIDTH = 1400
const DOCK_MAX_HEIGHT = 900

// DESIGN §2.1「用户输入起止」→ listening；停止输入该窗口后回落。
const TYPING_IDLE_MS = 2500

// 附件扩展名分拣：视频容器与后端白名单一致（mp4/mov，供应商实测 webm 被拒）；
// 图片同步支持 HEIC/HEIF（iPhone 截图）/TIFF/AVIF/JXL（next-gen）。
const IMAGE_EXT = /\.(png|jpe?g|gif|webp|bmp|heic|heif|tiff?|avif|jxl)$/i
const VIDEO_EXT = /\.(mp4|mov)$/i

// Electron 32+ 移除了 File.path——剪贴板/拖拽文件的真实路径只能经 webUtils 桥接。
const webUtilsBridge = (): { getPathForFile: (f: File) => string } | undefined =>
  (window as unknown as { spiritagentWebUtils?: { getPathForFile: (f: File) => string } }).spiritagentWebUtils

// 待发送附件：图片占本地路径或 data URL；视频附加即上传换取会话级 URL，就绪后才能发送。
type PendingAttachment =
  | { type: 'image'; value: string }
  | {
      type: 'video'
      fileName: string
      path: string
      status: 'error' | 'ready' | 'uploading'
      url?: string
      error?: string
    }

async function ensureChatSession(): Promise<string> {
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

// 视频附加即上传（本地后端 <1s）：本地模式下超 50MB 会被后端 413 拒绝并在 error 里给出指引。
async function attachVideoFile(
  path: string,
  setPending: React.Dispatch<React.SetStateAction<PendingAttachment | null>>
): Promise<void> {
  const fileName = path.split(/[\\/]/).pop() ?? path

  setPending({ type: 'video', fileName, path, status: 'uploading' })

  try {
    const sessionId = await ensureChatSession()
    const result = await window.spiritagent.uploadVideoForAttach({ path, sessionId })

    setPending({ type: 'video', fileName, path, status: 'ready', url: result.url })
  } catch (err) {
    setPending({
      type: 'video',
      fileName,
      path,
      status: 'error',
      error: err instanceof Error ? err.message : String(err)
    })
  }
}

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

interface ChatDockProps {
  onClose: () => void
  onOpenVoiceCall?: () => void
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

// 附加态缩略图（DESIGN §6.1 粘贴/拖入图片）：本地路径走媒体源解析通道取图，点击进全屏查看器。
function PendingImageThumb({ path }: { path: string }): React.JSX.Element {
  const src = useResolvedMediaSrc({ type: 'image', url: path })

  return (
    <button
      className="block h-16 w-16 shrink-0 cursor-zoom-in overflow-hidden rounded-lg border border-white/12 bg-black/30 p-0 transition hover:border-white/30"
      onClick={() => openMediaViewer({ type: 'image', url: path })}
      type="button"
    >
      {src ? (
        <img alt="待发送图片" className="block h-full w-full object-cover" src={src} />
      ) : (
        <span className="flex h-full w-full items-center justify-center text-[10px] text-white/40">加载中…</span>
      )}
    </button>
  )
}

export function ChatDock({ onClose, onOpenVoiceCall }: ChatDockProps): React.ReactElement {
  const list = useStore($chatMessageList)
  const lastAssistantStreaming = useStore($lastAssistantStreaming)
  const gatewayState = useStore($gatewayState)
  const portraitUrl = useStore($portraitUrl)
  const spriteEmotion = useStore($spriteEmotion)
  const spriteState = useStore($spriteState)
  const expressionAvatar = useStore($expressionAvatar)
  const customExpressions = useStore($expressions)
  const sessionListOpen = useStore($sessionListOpen)
  // 三个列表 atom 的订阅只为标题兜底链的响应性（findSessionInfo 会读它们）。
  useStore($sessions)
  useStore($archivedSessions)
  useStore($searchResults)
  const viewport = useStore($viewport)
  const { requestGateway } = useGatewayRequest()
  const [text, setText] = useState('')
  const [pending, setPending] = useState<PendingAttachment | null>(null)
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

  // Esc 优先收起会话抽屉，不打断正在输入的正文。
  useEffect(() => {
    if (!sessionListOpen) {
      return
    }

    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape' && !e.defaultPrevented) {
        e.preventDefault()
        setSessionListOpen(false)
      }
    }

    window.addEventListener('keydown', onKey)

    return () => window.removeEventListener('keydown', onKey)
  }, [sessionListOpen])

  // 监听从 SpriteStage 投喂的外部文件（DESIGN §6.3「文件投喂」）：
  // 把首个媒体文件（图进缩略图槽、视频走上传）装进附件槽，其他路径暂存到 ref 留给 send() 一并提交。
  // 注意：useStore 会立即同步当前值；mount 之后丢进 atom 的 payload 也会触发再次渲染，
  // 所以不需要再单独读 .get() 兜底。
  const externalPathsRef = useRef<string[]>([])
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
        setPending({ type: 'image', value: first })
      }

      externalPathsRef.current = [...mediaPaths.slice(1), ...otherPaths]
    } else {
      const names = otherPaths.map(p => p.split(/[\\/]/).pop() ?? p).join('、')
      setText(t => (t ? `${t}\n${names}` : names))
      externalPathsRef.current = []
    }

    clearExternalAttachment()
  }, [pendingExternal])

  useEffect(() => {
    scrollRef.current?.scrollTo?.({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [list])

  const onPaste = async (e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items

    if (!items) {
      return
    }

    for (const item of items) {
      // 从资源管理器复制的视频文件：取真实路径走上传（图片位图粘贴走 saveClipboardImage 分支）。
      if (item.kind === 'file' && item.type.startsWith('video/')) {
        const file = item.getAsFile()
        const path = file ? webUtilsBridge()?.getPathForFile(file) : undefined

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
            setPending({ type: 'image', value: path })
          }
        } catch {
          /* 忽略剪贴板读取失败 */
        }

        return
      }
    }
  }

  // DESIGN §6.1「支持拖拽文件」：面板本体也是投喂入口——解析真实路径后走与
  // 精灵投喂同一条附件管线（首个媒体进附件槽、其余随 send() 提交）。
  const onDrop = async (e: React.DragEvent) => {
    const files = Array.from(e.dataTransfer?.files ?? [])

    if (files.length === 0) {
      return
    }

    const paths: string[] = []

    for (const f of files) {
      try {
        const p = webUtilsBridge()?.getPathForFile(f)

        if (p) {
          paths.push(p)
        }
      } catch {
        /* 单个文件解析失败不影响其他文件 */
      }
    }

    if (paths.length > 0) {
      e.preventDefault()
      pushExternalAttachment(paths)
    }
  }

  // 附件按钮：图片进缩略图槽、视频走上传、其余类型以文件名入正文说明。
  const pickAttachment = async (): Promise<void> => {
    const [path] = await window.spiritagent.selectPaths({
      filters: [
        {
          extensions: [
            'png',
            'jpg',
            'jpeg',
            'gif',
            'webp',
            'bmp',
            'heic',
            'heif',
            'tiff',
            'tif',
            'avif',
            'jxl',
            'mp4',
            'mov'
          ],
          name: '图片与视频'
        }
      ],
      multiple: false,
      title: '添加附件'
    })

    if (!path) {
      return
    }

    if (VIDEO_EXT.test(path)) {
      await attachVideoFile(path, setPending)
    } else if (IMAGE_EXT.test(path)) {
      setPending({ type: 'image', value: path })
    } else {
      const name = path.split(/[\\/]/).pop() ?? path

      setText(t => (t ? `${t}\n附件：${name}` : `附件：${name}`))
    }
  }

  const send = async () => {
    const trimmed = text.trim()

    if ((!trimmed && !pending) || sending || (pending?.type === 'video' && pending.status !== 'ready')) {
      return
    }

    if (gatewayState !== 'open') {
      return
    }

    setSending(true)

    try {
      const id = await ensureChatSession()
      let fullText = trimmed
      // 发送附件（进 prompt.submit 的多模态 parts：图片 data URL / 视频上传 URL）与
      // 展示附件（data URL 或可渲染 URL，供气泡媒体卡取图）分开收集：图片本地路径
      // 过不了后端附件校验，只允许作渲染源。
      const attachments: ChatAttachment[] = []
      const displayAttachments: ChatAttachment[] = []

      if (pending?.type === 'image') {
        // 本地图片优先以 data URL 附件直发多模态（后端转 input_image parts，视觉链路接手）；
        // 读取失败（不可读/超体量）才降级路径模式：@file: 指令进正文，LLM 走文件工具读取。
        let dataUrl: string | null = pending.value.startsWith('data:') ? pending.value : null

        if (!dataUrl) {
          try {
            dataUrl = await window.spiritagent.readImageForAttach(pending.value)
          } catch {
            /* 降级路径模式 */
          }
        }

        if (dataUrl) {
          attachments.push({ type: 'image', url: dataUrl })
          displayAttachments.push({ type: 'image', url: dataUrl })
        } else {
          const ref = await requestGateway<{ ref_text?: string }>('image.attach', {
            session_id: id,
            path: pending.value
          })

          if (ref.ref_text) {
            fullText = `${fullText}\n${ref.ref_text}`.trim()
            displayAttachments.push({ type: 'image', url: pending.value })
          }
        }
      } else if (pending?.type === 'video' && pending.url) {
        attachments.push({ type: 'video', url: pending.url })
        displayAttachments.push({ type: 'video', url: pending.url })
      }

      // 同时附上 SpriteStage 投喂的多余文件路径（非媒体文件作为 reference，保留文本里说明）
      const extra = externalPathsRef.current

      if (extra.length > 0) {
        const names = extra.map(p => p.split(/[\\/]/).pop() ?? p).join('、')
        fullText = fullText ? `${fullText}\n附件：${names}` : `附件：${names}`
      }

      externalPathsRef.current = []

      // 展示层：媒体卡即内容，纯附件消息不留占位文案；仅附件与正文全空时兜底。
      pushUserMessage(
        fullText || (displayAttachments.length ? '' : pending?.type === 'video' ? '（视频）' : '（图片）'),
        displayAttachments.length ? displayAttachments : undefined
      )
      setText('')
      setPending(null)
      setSpriteState('thinking')

      pushPendingPrompt({
        text: fullText || (pending?.type === 'video' ? '请看这段视频' : '请看这张图'),
        attachments: attachments.length ? attachments : undefined
      })
      schedulePendingFlush()
    } catch (err) {
      setAssistantError(err instanceof Error ? err.message : '发送失败')
      setSpriteState('idle')
      setPending(null)
    } finally {
      setSending(false)
    }
  }

  const lastIsUser = list[list.length - 1]?.role === 'user'
  const showTyping = lastIsUser && gatewayState === 'open'

  const isGenerating = gatewayState === 'open' && (showTyping || lastAssistantStreaming)

  const handleStop = async () => {
    cancelAutoVoice()
    // 用户主动停止是合并窗口的收尾信号（DESIGN §6.6）——排队的连发消息立即提交，
    // 不能丢弃；中断只针对当前生成回合。in-flight 标记先清，冲刷才会真正发出。
    cancelPendingFlush()
    $chatTurnInFlight.set(false)
    const sid = $chatSessionId.get()

    if (sid) {
      try {
        await requestGateway('session.interrupt', { session_id: sid })
      } catch {
        /* 尽力而为 */
      }
    }

    void window.spiritagent?.runnerCancel?.().catch(() => {})

    const lastItem = $chatMessageList.get().at(-1)
    const lastBody = lastItem ? $chatMessageBodies.get()[lastItem.id] : undefined

    if (lastItem?.role === 'assistant' && lastBody?.streaming && lastBody.text.trim()) {
      finalizeAssistantMessage()
    } else {
      setAssistantCancelled()
    }

    setSpriteState('idle', { force: true })
    submitPendingBatch()
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

      case 'working':
        return { label: '专注工作中', icon: '💼' }

      default:
        return { label: '平静温和', icon: '😊' }
    }
  }, [spriteEmotion, spriteState, customExpressions])

  const currentSessionTitle = findSessionInfo($chatSessionId.get() ?? '')?.title || '日常对话'

  return (
    <div className="fixed inset-0 z-40 pointer-events-none">
      <div
        className="relative flex flex-row overflow-hidden rounded-2xl border border-white/12 bg-surface-panel text-white shadow-2xl"
        onDragOver={e => e.preventDefault()}
        onDrop={e => void onDrop(e)}
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

        {sessionListOpen && <SessionDrawer onClose={() => setSessionListOpen(false)} />}

        {/* Left Column: Visual Anchor & Character Emotion Status (Draggable) */}
        <div
          className="flex w-52 flex-shrink-0 cursor-grab flex-col items-center justify-between border-r border-white/10 bg-surface-chrome p-4 select-none active:cursor-grabbing"
          {...dragBind}
          title="拖动以移动对话框"
        >
          <div className="flex flex-col items-center w-full">
            {/* Character Avatar with subtle glow and framing */}
            <div className="relative group mt-1">
              <div className="relative h-36 w-36 overflow-hidden rounded-2xl border border-white/12 bg-white/5 shadow-xl transition duration-300 group-hover:border-white/25">
                {(expressionAvatar?.dataUrl ?? portraitUrl) ? (
                  <img
                    alt="角色形象"
                    className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
                    src={expressionAvatar?.dataUrl ?? portraitUrl ?? undefined}
                  />
                ) : (
                  <div className="flex h-full w-full flex-col items-center justify-center bg-linear-to-b from-white/10 to-white/5 p-4 text-center">
                    <Sparkles className="size-7 animate-pulse text-white/30" />
                    <span className="mt-2 text-[11px] text-white/40">伙伴形象</span>
                  </div>
                )}
              </div>
              {/* Status Badge floating at bottom of avatar */}
              <div className="absolute -bottom-2.5 left-1/2 -translate-x-1/2 flex items-center gap-1.5 rounded-full border border-white/12 bg-surface-panel px-2.5 py-0.5 text-[10px] text-white/90 shadow-md whitespace-nowrap">
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

            {/* Current Emotion Status Display */}
            <div className="mt-6 flex flex-col items-center text-center w-full px-2">
              <div className="flex items-center gap-1.5 rounded-full border border-white/12 bg-white/5 px-3 py-1 text-xs text-white/90 shadow-sm">
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
        <div className="flex flex-1 flex-col min-w-0 bg-surface-panel">
          {/* Header Bar */}
          <div
            className="flex cursor-grab items-center justify-between gap-2 border-b border-white/10 px-3 py-2 active:cursor-grabbing"
            {...dragBind}
            title="拖动以移动对话框"
          >
            <div className="flex min-w-0 items-center gap-1.5">
              <button
                aria-label="切换对话"
                className={cn(BTN_ICON, sessionListOpen && 'bg-white/10 text-white')}
                onClick={() => setSessionListOpen(!sessionListOpen)}
                title="切换历史对话"
                type="button"
              >
                <PanelLeft />
              </button>
              <span className="truncate text-sm font-medium text-white/90">{currentSessionTitle}</span>
            </div>
            <div className="flex items-center gap-0.5">
              {onOpenVoiceCall && (
                <button
                  aria-label="语音通话"
                  className={BTN_ICON}
                  onClick={onOpenVoiceCall}
                  title="开启实时语音通话模式"
                  type="button"
                >
                  <Phone />
                </button>
              )}
              <button aria-label="关闭对话" className={BTN_ICON} onClick={onClose} type="button">
                <X />
              </button>
            </div>
          </div>

          {/* Messages Area */}
          <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4" ref={scrollRef}>
            {list.length === 0 && !sending && (
              <p className="mt-8 text-center text-sm text-white/40">说点什么，或粘贴图片/视频给我看看～</p>
            )}
            {list.map(item => (
              <MessageBubble key={item.id} message={item} />
            ))}
            {showTyping && (
              <div className="flex justify-start">
                <span className="flex items-center gap-1 rounded-2xl rounded-bl-sm border border-white/8 bg-surface-card px-3.5 py-2.5">
                  <i className="size-1.5 animate-bounce rounded-full bg-white/40 [animation-delay:0ms]" />
                  <i className="size-1.5 animate-bounce rounded-full bg-white/40 [animation-delay:150ms]" />
                  <i className="size-1.5 animate-bounce rounded-full bg-white/40 [animation-delay:300ms]" />
                </span>
              </div>
            )}
            <ChatScrollAutoFollow scrollRef={scrollRef} />
          </div>

          {pending?.type === 'image' && (
            <div className="flex items-center gap-2 border-t border-white/10 px-4 py-2 text-xs text-white/60">
              <PendingImageThumb path={pending.value} />
              <span>{sending ? '图片发送中…' : '已附加图片，点击可查看'}</span>
              {!sending && (
                <button
                  aria-label="移除附加图片"
                  className="rounded-md p-1 text-white/40 transition hover:bg-white/10 hover:text-white"
                  onClick={() => setPending(null)}
                  type="button"
                >
                  <X className="size-3.5" />
                </button>
              )}
            </div>
          )}
          {pending?.type === 'video' && (
            <div className="flex items-center gap-2 border-t border-white/10 px-4 py-2 text-xs text-white/60">
              <Video className="size-4 shrink-0 text-white/50" />
              <span className="max-w-40 shrink truncate">{pending.fileName}</span>
              {pending.status === 'uploading' && <span className="text-white/40">上传中…</span>}
              {pending.status === 'ready' && <span>已就绪</span>}
              {pending.status === 'error' && (
                <>
                  <span className="min-w-0 flex-1 truncate text-amber-300/80" title={pending.error}>
                    {pending.error}
                  </span>
                  <button
                    className="shrink-0 rounded-md px-1.5 py-0.5 text-white/50 transition hover:bg-white/10 hover:text-white"
                    onClick={() => void attachVideoFile(pending.path, setPending)}
                    type="button"
                  >
                    重试
                  </button>
                </>
              )}
              {!sending && (
                <button
                  aria-label="移除附加视频"
                  className="shrink-0 rounded-md p-1 text-white/40 transition hover:bg-white/10 hover:text-white"
                  onClick={() => setPending(null)}
                  type="button"
                >
                  <X className="size-3.5" />
                </button>
              )}
            </div>
          )}

          {/* Input Area */}
          <div className="border-t border-white/10 p-3">
            {gatewayState !== 'open' && <p className="mb-2 text-center text-xs text-amber-300/70">正在连接…</p>}
            <div className="flex items-end gap-2">
              <textarea
                className="max-h-32 min-h-[38px] flex-1 resize-none rounded-lg border border-white/12 bg-white/5 px-3 py-2 text-sm leading-normal text-white outline-none placeholder:text-white/30 focus:border-accent/70"
                onChange={e => {
                  setText(e.target.value)
                  onTyping()
                }}
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
                aria-label="添加附件"
                className="inline-flex h-[38px] shrink-0 items-center justify-center rounded-lg border border-white/12 bg-white/5 text-white/70 transition hover:bg-white/10 hover:text-white"
                onClick={() => void pickAttachment()}
                title="附加图片或视频（mp4/mov）"
                type="button"
              >
                <Paperclip className="size-4" />
              </button>
              <button
                className={`inline-flex h-[38px] shrink-0 items-center justify-center gap-1.5 rounded-lg border px-3 text-xs font-medium transition ${
                  recording
                    ? 'border-rose-400/70 bg-rose-500/25 text-white animate-pulse'
                    : 'border-white/12 bg-white/5 text-white/70 hover:bg-white/10 hover:text-white'
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
                {recording ? '松开发送' : <Mic className="size-4" />}
              </button>
              <button
                className={cn(BTN_PRIMARY, 'h-[38px] px-4 text-sm')}
                disabled={
                  !isGenerating &&
                  (sending ||
                    gatewayState !== 'open' ||
                    (!text.trim() && !pending) ||
                    (pending?.type === 'video' && pending.status !== 'ready'))
                }
                onClick={() => void (isGenerating ? handleStop() : send())}
                type="button"
              >
                {isGenerating ? '停止' : '发送'}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
