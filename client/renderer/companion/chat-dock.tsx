import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { $expressions } from '@/companion/3d/model-store'
import { cancelAutoVoice } from '@/companion/auto-voice-stream'
import { useGatewayRequest } from '@/companion/boot/use-gateway-request'
import {
  $chatDraftFromUndo,
  $chatMessageBodies,
  $chatMessageList,
  $chatSessionId,
  $chatSessionKind,
  $chatStreamingTick,
  $chatTurnInFlight,
  $lastAssistantStreaming,
  $pendingExternalAttachment,
  $pendingPromptBatch,
  cancelPendingFlush,
  clearExternalAttachment,
  finalizeAssistantMessage,
  hydrateChatMessages,
  markAssistantTerminal,
  type PendingAttachment,
  pushExternalAttachment,
  pushPendingPrompt,
  pushStatusPill,
  pushUserMessage,
  schedulePendingFlush,
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
import { SpiritAgentRpcError, SpiritAgentRpcErrorCode } from '@/shared/lib/gateway-protocol/json-rpc-gateway'
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
import { fuzzyFilterCommands, parseSlashInput, type SlashCommandMeta } from '@/shared/lib/slash-commands'
import { cn } from '@/shared/lib/utils'
import { BorderBeam, BTN_ICON, HudCorners } from '@/shared/panel'
import { $gatewayState } from '@/shared/store/gateway'
import type { ChatAttachment, SessionMessage } from '@/shared/types/spiritagent'

import { MessageBubble } from './chat-dock-message-bubble'
import { useResolvedMediaSrc } from './chat-media-src'
import { ChatParamsPanel } from './chat/chat-params-panel'
import { ContextProgressBar } from './chat/context-progress-bar'
import { SessionDrawer } from './chat/session-drawer'
import { SlashCommandPopover } from './chat/slash-command-popover'
import { usePanelDrag } from './hooks/use-panel-drag'
import { usePanelResize } from './hooks/use-panel-resize'
import { useVoiceRecorder } from './hooks/use-voice-recorder'
import { openMediaViewer } from './media-viewer-overlay'
import {
  $sessionListOpen,
  findSessionInfo,
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

// 附件扩展名分拣：视频容器与后端白名单一致（mp4/mov，供应商实测 webm 被拒）；
// 图片同步支持 HEIC/HEIF（iPhone 截图）/TIFF/AVIF/JXL（next-gen）。
const IMAGE_EXT = /\.(png|jpe?g|gif|webp|bmp|heic|heif|tiff?|avif|jxl)$/i
const VIDEO_EXT = /\.(mp4|mov)$/i

// Electron 32+ 移除了 File.path——剪贴板/拖拽文件的真实路径只能经 webUtils 桥接。
const webUtilsBridge = (): { getPathForFile: (f: File) => string } | undefined =>
  (window as unknown as { spiritagentWebUtils?: { getPathForFile: (f: File) => string } }).spiritagentWebUtils

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

interface SlashCommandRpcResult {
  command: string
  result: {
    status: 'ok' | 'error'
    message: string
    payload?: Record<string, unknown> | null
    hydrate?: boolean
  }
}

function slashErrorToMessage(err: unknown): string {
  if (err instanceof SpiritAgentRpcError) {
    if (err.code === SpiritAgentRpcErrorCode.SlashConfirmRequired) {
      return '该命令需要二次确认'
    }

    if (err.code === SpiritAgentRpcErrorCode.SlashBusy) {
      return '请先停止当前生成'
    }

    if (err.code === SpiritAgentRpcErrorCode.SlashGeneric) {
      return '命令执行失败'
    }

    if (err.code === SpiritAgentRpcErrorCode.InvalidParams) {
      const suggestions = (err.data as { suggestions?: string[] } | undefined)?.suggestions

      if (suggestions?.length) {
        return `未知命令。可选: ${suggestions.map(s => `/${s}`).join(', ')}`
      }

      return '未知命令'
    }
  }

  const msg = err instanceof Error ? err.message : String(err)

  return msg || '命令执行失败'
}

/**
 * 弹层是否应该拦截当前文本：避免「边发图片边清空」歧义。
 * 与 send() 的拦截分支共用同一道闸，保证弹层与兜底路径行为一致。
 */
function slashPreCheck(pending: PendingAttachment | null, sending: boolean): string | null {
  if (sending) {
    return '上一条命令还在执行中'
  }

  if (pending) {
    return '请先发送或取消附件再执行命令'
  }

  return null
}

/**
 * 单条 slash 命令的执行入口：confirm → RPC → hydrate/pill → 错误映射。所有三处调用方
 * （send() 兜底路径、textarea onKeyDown 选中、SlashCommandPopover 点击）都走这里。
 *
 * 需确认的命令每次调用都会弹 window.confirm——PROTOCOL §1.9 明确要求前端必须弹，
 * 即便弹层/Enter 路径已经把意图表达得很清楚。
 */
async function executeSlashCommand(
  cmd: SlashCommandMeta,
  args: string[],
  opts: {
    requestGateway: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>
    onStart?: () => void
    onFinish?: () => void
  }
): Promise<void> {
  const { requestGateway, onStart, onFinish } = opts

  if (cmd.requiresConfirmation && !window.confirm(`执行 /${cmd.name}？该操作会影响历史消息。`)) {
    return
  }

  try {
    onStart?.()

    const sid = await ensureChatSession()

    const result = await requestGateway<SlashCommandRpcResult>('command.dispatch', {
      session_id: sid,
      command: cmd.name,
      args,
      confirmed: true
    })

    const r = result.result

    if (r.status === 'ok') {
      // hydrate=true 时，payload.messages 已包含服务端写入的 status_cleared / compress_summary
      // marker 行——前端 hydrateChatMessages 后再 pushStatusPill 会产生重复 pill，
      // 所以 hydrate 路径只更新消息列表，不再追加 status_command_result。
      if (r.hydrate && r.payload) {
        const raw = (r.payload as { messages?: unknown }).messages

        if (Array.isArray(raw)) {
          hydrateChatMessages(raw as SessionMessage[])
        }
      } else {
        pushStatusPill('status_command_result', r.message)
      }
    } else {
      markAssistantTerminal({ error: r.message })
    }
  } catch (err) {
    markAssistantTerminal({ error: slashErrorToMessage(err) })
  } finally {
    onFinish?.()
  }
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
      className="block h-16 w-16 shrink-0 cursor-zoom-in overflow-hidden rounded-lg border border-line-standard bg-fill-trough p-0 transition hover:border-line-strong"
      onClick={() => openMediaViewer({ type: 'image', url: path })}
      type="button"
    >
      {src ? (
        <img alt="待发送图片" className="block h-full w-full object-cover" src={src} />
      ) : (
        <span className="flex h-full w-full items-center justify-center text-[10px] text-faint">加载中…</span>
      )}
    </button>
  )
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
  // 三个列表 atom 的订阅只为标题兜底链的响应性（findSessionInfo 会读它们）。
  useStore($sessions)
  useStore($archivedSessions)
  useStore($searchResults)
  const viewport = useStore($viewport)
  const { requestGateway } = useGatewayRequest()
  const [text, setText] = useState('')
  const [pending, setPending] = useState<PendingAttachment | null>(null)
  const [sending, setSending] = useState(false)
  const [attachMenuOpen, setAttachMenuOpen] = useState(false)
  const attachMenuRef = useRef<HTMLDivElement>(null)

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

  // 当用户改动文本时重置 dismissed 与高亮项。
  useEffect(() => {
    setSlashDismissed(false)
    setSlashHighlightIndex(0)
  }, [text])

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
  }, [])

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
        setPending({ type: 'image', value: first, fileName: first.split(/[\\/]/).pop() ?? first })
      }

      externalPathsRef.current = [...mediaPaths.slice(1), ...otherPaths]
    } else if (otherPaths.length === 1) {
      const first = otherPaths[0]
      const fileName = first.split(/[\\/]/).pop() ?? first
      setPending({ type: 'file', fileName, path: first })
      externalPathsRef.current = []
    } else {
      const names = otherPaths.map(p => p.split(/[\\/]/).pop() ?? p).join('、')
      setText(t => (t ? `${t}\n${names}` : names))
      externalPathsRef.current = []
    }

    clearExternalAttachment()
  }, [pendingExternal])

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
  }, [chatSessionId])

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

  // IM 桥接会话在桌面端只读查看。
  // 优先 $chatSessionKind（服务端权威）；重启后列表尚未加载的瞬间窗口退到 findSessionInfo。
  const chatSessionKind = useStore($chatSessionKind)

  const sessionKind = chatSessionKind || findSessionInfo(chatSessionId ?? '')?.kind || ''
  const isReadOnlySession = sessionKind === 'im'

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
            setPending({ type: 'image', value: path, fileName: path.split(/[\\/]/).pop() ?? path })
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
  const onDrop = async (e: React.DragEvent): Promise<void> => {
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

  // 附件选择：支持文件、文件夹、图片、视频 4 种类型
  const pickFile = async (): Promise<void> => {
    setAttachMenuOpen(false)

    try {
      const [path] = await window.spiritagent.selectPaths({
        multiple: false,
        title: '选择文件'
      })

      if (!path) {
        return
      }

      if (VIDEO_EXT.test(path)) {
        await attachVideoFile(path, setPending)
      } else if (IMAGE_EXT.test(path)) {
        setPending({ type: 'image', value: path, fileName: path.split(/[\\/]/).pop() ?? path })
      } else {
        const fileName = path.split(/[\\/]/).pop() ?? path
        setPending({ type: 'file', fileName, path })
      }
    } catch {
      /* 用户取消或读取失败 */
    }
  }

  const pickFolder = async (): Promise<void> => {
    setAttachMenuOpen(false)

    try {
      const [path] = await window.spiritagent.selectPaths({
        directories: true,
        multiple: false,
        title: '选择文件夹'
      })

      if (!path) {
        return
      }

      const folderName = path.split(/[\\/]/).filter(Boolean).pop() ?? path
      setPending({ type: 'folder', folderName, path })
    } catch {
      /* 用户取消或读取失败 */
    }
  }

  const pickImage = async (): Promise<void> => {
    setAttachMenuOpen(false)

    try {
      const [path] = await window.spiritagent.selectPaths({
        filters: [
          {
            extensions: ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'heic', 'heif', 'tiff', 'tif', 'avif', 'jxl'],
            name: '图片文件'
          }
        ],
        multiple: false,
        title: '选择图片'
      })

      if (!path) {
        return
      }

      setPending({ type: 'image', value: path, fileName: path.split(/[\\/]/).pop() ?? path })
    } catch {
      /* 用户取消或读取失败 */
    }
  }

  const pickVideo = async (): Promise<void> => {
    setAttachMenuOpen(false)

    try {
      const [path] = await window.spiritagent.selectPaths({
        filters: [
          {
            extensions: ['mp4', 'mov'],
            name: '视频文件'
          }
        ],
        multiple: false,
        title: '选择视频'
      })

      if (!path) {
        return
      }

      await attachVideoFile(path, setPending)
    } catch {
      /* 用户取消或读取失败 */
    }
  }

  const send = async (): Promise<void> => {
    if (isReadOnlySession) {
      return
    }

    const trimmed = text.trim()

    if ((!trimmed && !pending) || sending || (pending?.type === 'video' && pending.status !== 'ready')) {
      return
    }

    if (gatewayState !== 'open') {
      return
    }

    // Slash 命令拦截：无论弹层是否开启，以 `/` 开头的命令格式文本均改走 command.dispatch。
    const parsed = parseSlashInput(trimmed)

    if (parsed) {
      const preCheck = slashPreCheck(pending, sending)

      if (preCheck) {
        markAssistantTerminal({ error: preCheck })

        return
      }

      if (parsed.command) {
        const cmd = parsed.command

        await executeSlashCommand(cmd, parsed.args, {
          requestGateway,
          onStart: () => {
            setSending(true)
            setText('')
          },
          onFinish: () => setSending(false)
        })

        return
      }

      // 命中 `/foo` 但未识别本地命令：toast 提示，**保留** 输入文本让用户修改。
      // 不退回 prompt.submit，避免 `/foo` 被 LLM 当真发出去消耗 token。
      markAssistantTerminal({ error: `未知命令: /${parsed.name}。试试 /help 查看可用命令。` })

      return
    }

    setSending(true)

    try {
      const id = await ensureChatSession()
      let fullText = trimmed
      let promptText = trimmed
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
            promptText = `${promptText}\n${ref.ref_text}`.trim()
            displayAttachments.push({ type: 'image', url: pending.value })
          }
        }
      } else if (pending?.type === 'video' && pending.url) {
        attachments.push({ type: 'video', url: pending.url })
        displayAttachments.push({ type: 'video', url: pending.url })
      } else if (pending?.type === 'file') {
        const fileRef = `[文件: ${pending.fileName}] ${pending.path}`
        fullText = fullText ? `${fullText}\n${fileRef}` : fileRef
        const fileDirective = `@file:${pending.path}`
        promptText = promptText ? `${promptText}\n${fileDirective}` : fileDirective
      } else if (pending?.type === 'folder') {
        const folderRef = `[文件夹: ${pending.folderName}] ${pending.path}`
        fullText = fullText ? `${fullText}\n${folderRef}` : folderRef
        const folderDirective = `@folder:${pending.path}`
        promptText = promptText ? `${promptText}\n${folderDirective}` : folderDirective
      }

      // 同时附上 SpriteStage 投喂的多余文件路径（非媒体文件作为 reference，保留文本里说明）
      const extra = externalPathsRef.current

      if (extra.length > 0) {
        const names = extra.map(p => p.split(/[\\/]/).pop() ?? p).join('、')
        fullText = fullText ? `${fullText}\n附件：${names}` : `附件：${names}`
        const extraDirectives = extra.map(p => `@file:${p}`).join('\n')
        promptText = promptText ? `${promptText}\n${extraDirectives}` : extraDirectives
      }

      externalPathsRef.current = []

      // 展示层：媒体卡即内容，纯附件消息不留占位文案；仅附件与正文全空时兜底。
      const displayPlaceholder = displayAttachments.length
        ? ''
        : pending?.type === 'video'
          ? '（视频）'
          : pending?.type === 'image'
            ? '（图片）'
            : pending?.type === 'file'
              ? `[文件] ${pending.fileName}`
              : pending?.type === 'folder'
                ? `[文件夹] ${pending.folderName}`
                : ''

      const promptFallback =
        pending?.type === 'video'
          ? '请看这段视频'
          : pending?.type === 'image'
            ? '请看这张图'
            : pending?.type === 'file'
              ? `@file:${pending.path}`
              : pending?.type === 'folder'
                ? `@folder:${pending.path}`
                : ''

      pushUserMessage(fullText || displayPlaceholder, displayAttachments.length ? displayAttachments : undefined)
      setText('')
      setPending(null)
      setSpriteState('thinking')

      pushPendingPrompt({
        text: promptText || promptFallback,
        attachments: attachments.length ? attachments : undefined
      })
      schedulePendingFlush()
    } catch (err) {
      markAssistantTerminal({ error: err instanceof Error ? err.message : '发送失败' })
      setSpriteState('idle')
      setPending(null)
    } finally {
      setSending(false)
    }
  }

  const isTurnPendingOrInFlight = pendingPromptBatch.length > 0 || chatTurnInFlight
  const showTyping = isTurnPendingOrInFlight && !lastAssistantStreaming && gatewayState === 'open'

  const isGenerating = gatewayState === 'open' && (isTurnPendingOrInFlight || lastAssistantStreaming)

  const handleStop = async (): Promise<void> => {
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
      markAssistantTerminal({ cancelled: true })
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
        className="relative flex flex-row overflow-hidden rounded-2xl border border-line-strong bg-surface-panel text-strong shadow-2xl border-beam-container"
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
          {...dragBind}
          title="拖动以移动对话框"
        >
          <div className="flex flex-col items-center w-full min-h-0 overflow-y-auto no-scrollbar">
            {/* Character Avatar with subtle glow and framing */}
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

            {/* Current Emotion Status Display */}
            <div className="mt-4 flex flex-col items-center text-center w-full px-1">
              <div className="flex items-center gap-1.5 rounded-full border border-line-standard bg-fill-faint px-2.5 py-0.5 text-xs text-strong shadow-sm">
                <span className="text-sm">{currentMood.icon}</span>
                <span className="font-medium tracking-wide text-[11px]">{currentMood.label}</span>
              </div>
            </div>
          </div>

          {/* Bottom Section: Session Parameters & Status Hint */}
          <div className="w-full flex flex-col items-center gap-2 pt-2 border-t border-line-hairline shrink-0">
            <ChatParamsPanel />
            <p className="text-[10px] text-faint pt-0.5">{gatewayState === 'open' ? '随时倾听中' : '网络连接中…'}</p>
          </div>
        </div>

        {/* Right Column: Chat Stream & Input */}
        <div className="flex flex-1 flex-col min-w-0 bg-surface-panel">
          {/* Header Bar */}
          <div
            className="flex cursor-grab items-center justify-between gap-2 border-b border-line-standard px-3 py-2 active:cursor-grabbing"
            {...dragBind}
            title="拖动以移动对话框"
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

          {/* Messages Area */}
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

          {/* Input Area: Two-row Card Layout */}
          <div className="border-t border-line-standard p-3 pt-2.5 flex flex-col gap-1.5">
            {gatewayState !== 'open' && <p className="mb-1 text-center text-xs text-amber-300/70">正在连接…</p>}
            {isReadOnlySession && <p className="mb-1 text-center text-xs text-faint">IM 对话 · 只读</p>}

            <div className="relative flex flex-col gap-2 rounded-xl border border-line-standard bg-fill-faint p-2.5 transition focus-within:border-accent/60 focus-within:bg-fill-hover shadow-sm">
              {/* Row 1: Multiline Textarea & Slash Popover */}
              <div className="relative w-full">
                <textarea
                  className="max-h-32 min-h-[42px] w-full resize-none border-0 bg-transparent p-0 text-sm leading-relaxed text-strong outline-none placeholder:text-faint disabled:pointer-events-none disabled:opacity-40"
                  disabled={isReadOnlySession}
                  onChange={e => {
                    setText(e.target.value)
                    onTyping()
                  }}
                  onKeyDown={e => {
                    // Slash 命令弹层：方向键改高亮、Tab/Enter 选中、Esc 关闭。
                    if (slashPopoverOpen && slashItems.length > 0) {
                      if (e.key === 'ArrowDown') {
                        e.preventDefault()
                        setSlashHighlightIndex(i => (i + 1) % slashItems.length)

                        return
                      }

                      if (e.key === 'ArrowUp') {
                        e.preventDefault()
                        setSlashHighlightIndex(i => (i - 1 + slashItems.length) % slashItems.length)

                        return
                      }

                      if (e.key === 'Tab') {
                        e.preventDefault()
                        const item = slashItems[slashHighlightIndex]

                        if (item) {
                          setText(`/${item.cmd.name} `)
                        }

                        return
                      }

                      if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault()
                        const item = slashItems[slashHighlightIndex]

                        if (item) {
                          const parsed = parseSlashInput(text.trim())
                          const args = parsed?.args ?? []

                          if (item.cmd.name === 'remember' && args.length === 0) {
                            setText(`/${item.cmd.name} `)
                            inputRef.current?.focus()

                            return
                          }

                          const preCheck = slashPreCheck(pending, sending)

                          if (preCheck) {
                            markAssistantTerminal({ error: preCheck })

                            return
                          }

                          void executeSlashCommand(item.cmd, args, {
                            requestGateway,
                            onStart: () => {
                              setSending(true)
                              setText('')
                            },
                            onFinish: () => setSending(false)
                          })
                        }

                        return
                      }

                      if (e.key === 'Escape') {
                        e.preventDefault()
                        // 仅关闭弹层，保留用户输入文本——Esc 不应该丢弃未发送的草稿。
                        setSlashDismissed(true)

                        return
                      }
                    }

                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault()
                      void send()
                    }
                  }}
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
                      const parsed = parseSlashInput(text.trim())
                      const args = parsed?.args ?? []

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

                      void executeSlashCommand(cmd, args, {
                        requestGateway,
                        onStart: () => {
                          setSending(true)
                          setText('')
                        },
                        onFinish: () => setSending(false)
                      })
                    }}
                    query={slashQuery}
                  />
                )}
              </div>

              {/* Pending Attachments inside card */}
              {pending?.type === 'image' && (
                <div className="flex items-center gap-2 rounded-lg bg-fill-faint border border-line-hairline px-2.5 py-1 text-xs text-body">
                  <PendingImageThumb path={pending.value} />
                  <span className="truncate flex-1 text-[11px] text-body">
                    {sending ? '图片发送中…' : pending.fileName || '已附加图片'}
                  </span>
                  {!sending && (
                    <button
                      aria-label="移除附加图片"
                      className="rounded-md p-1 text-faint transition hover:bg-fill-hover hover:text-strong"
                      onClick={() => setPending(null)}
                      type="button"
                    >
                      <X className="size-3" />
                    </button>
                  )}
                </div>
              )}
              {pending?.type === 'video' && (
                <div className="flex items-center gap-2 rounded-lg bg-fill-faint border border-line-hairline px-2.5 py-1 text-xs text-body">
                  <Video className="size-3.5 shrink-0 text-rose-400" />
                  <span className="max-w-40 shrink truncate text-[11px] text-body">{pending.fileName}</span>
                  {pending.status === 'uploading' && <span className="text-[10px] text-faint">上传中…</span>}
                  {pending.status === 'ready' && <span className="text-[10px] text-emerald-400">已就绪</span>}
                  {pending.status === 'error' && (
                    <>
                      <span className="min-w-0 flex-1 truncate text-[10px] text-amber-300/80" title={pending.error}>
                        {pending.error}
                      </span>
                      <button
                        className="shrink-0 rounded-md px-1.5 py-0.5 text-[10px] text-muted transition hover:bg-fill-hover hover:text-strong"
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
                      className="shrink-0 rounded-md p-1 text-faint transition hover:bg-fill-hover hover:text-strong"
                      onClick={() => setPending(null)}
                      type="button"
                    >
                      <X className="size-3" />
                    </button>
                  )}
                </div>
              )}
              {pending?.type === 'file' && (
                <div className="flex items-center gap-2 rounded-lg bg-fill-faint border border-line-hairline px-2.5 py-1 text-xs text-body">
                  <FileText className="size-3.5 shrink-0 text-accent" />
                  <div className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate text-[11px] font-medium text-strong" title={pending.path}>
                      {pending.fileName}
                    </span>
                  </div>
                  <span className="rounded bg-fill-hover px-1 py-0.2 text-[9px] text-faint">文件</span>
                  {!sending && (
                    <button
                      aria-label="移除附加文件"
                      className="shrink-0 rounded-md p-1 text-faint transition hover:bg-fill-hover hover:text-strong"
                      onClick={() => setPending(null)}
                      type="button"
                    >
                      <X className="size-3" />
                    </button>
                  )}
                </div>
              )}
              {pending?.type === 'folder' && (
                <div className="flex items-center gap-2 rounded-lg bg-fill-faint border border-line-hairline px-2.5 py-1 text-xs text-body">
                  <FolderOpen className="size-3.5 shrink-0 text-amber-400" />
                  <div className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate text-[11px] font-medium text-strong" title={pending.path}>
                      {pending.folderName}
                    </span>
                  </div>
                  <span className="rounded bg-fill-hover px-1 py-0.2 text-[9px] text-faint">文件夹</span>
                  {!sending && (
                    <button
                      aria-label="移除附加文件夹"
                      className="shrink-0 rounded-md p-1 text-faint transition hover:bg-fill-hover hover:text-strong"
                      onClick={() => setPending(null)}
                      type="button"
                    >
                      <X className="size-3" />
                    </button>
                  )}
                </div>
              )}

              {/* Row 2: Bottom Toolbar */}
              <div className="flex items-center justify-between gap-2 pt-1 border-t border-line-hairline">
                {/* Left corner: Attachment & Slash command */}
                <div className="flex items-center gap-1 shrink-0">
                  {/* Attachment menu trigger */}
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
                          onClick={() => void pickFile()}
                          type="button"
                        >
                          <FileText className="size-3.5 text-accent" />
                          <span>添加文件</span>
                        </button>
                        <button
                          className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs text-body transition hover:bg-fill-hover hover:text-strong text-left"
                          onClick={() => void pickFolder()}
                          type="button"
                        >
                          <FolderOpen className="size-3.5 text-amber-400" />
                          <span>添加文件夹</span>
                        </button>
                        <button
                          className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs text-body transition hover:bg-fill-hover hover:text-strong text-left"
                          onClick={() => void pickImage()}
                          type="button"
                        >
                          <ImageIcon className="size-3.5 text-emerald-400" />
                          <span>添加图片</span>
                        </button>
                        <button
                          className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs text-body transition hover:bg-fill-hover hover:text-strong text-left"
                          onClick={() => void pickVideo()}
                          type="button"
                        >
                          <Video className="size-3.5 text-rose-400" />
                          <span>添加视频</span>
                        </button>
                      </div>
                    )}
                  </div>

                  {/* Slash command button */}
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

                {/* Center: Context usage progress bar */}
                <div className="flex-1 min-w-0 px-2 flex items-center">
                  <ContextProgressBar />
                </div>

                {/* Right corner: Voice record & Send/Stop */}
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
