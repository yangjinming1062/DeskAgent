import { useStore } from '@nanostores/react'
import type React from 'react'
import { memo, useState } from 'react'

import { $portraitUrl } from '@/companion'
import { ChevronDown } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'

import { ChatMediaCard } from './chat-media-card'
import { ChatMessageCopyButton } from './chat-message-copy-button'
import { ChatMessageForkButton } from './chat-message-fork-button'
import { ChatMessagePlayButton } from './chat-message-play-button'
import { ChatMessageUndoButton } from './chat-message-undo-button'
import { $chatMessageBodies, $chatTurnInFlight, type ChatMessageBody, type ChatMessageListItem } from './chat-store'
import { ToolChipTimeline } from './tool-chip-timeline'

// 居中的元信息行，而非聊天气泡。Slash 命令结果与历史清空标记（详见 PROTOCOL §1.9）走同一形态。
const SYSTEM_PILL_SUBTYPES = new Set(['hint', 'daily_summary', 'status_cleared', 'status_command_result'])

// 上下文压缩检查点：居中的分界线式可折叠卡片，默认折叠、点击展开摘要全文。
// 走独立分支而不是 pill —— 视觉权重要让用户意识到这是个有信息量的节点。
const COMPRESS_CARD_SUBTYPES = new Set(['compress_summary'])

export type ConversationVariant = 'living' | 'workbench'

interface MessageBubbleProps {
  message: ChatMessageListItem
  variant?: ConversationVariant
}

// 戳 / 拖拽追踪：侧对齐但视觉上弱化。
const STATUS_TRACE_SUBTYPES = new Set(['status_interaction', 'status_reaction'])
// 仅肢体语言回复（无文字）：渲染为低权重 trace 提示。
const AFFECT_TRACE_SUBTYPE = 'status_affect'
// 后台视频完成的送达行：正文是给 LLM 的摘要，渲染端只显示媒体卡。
const MEDIA_STATUS_SUBTYPE = 'status_media'

// 路径模式降级时的 @file: 指令（含历史行里拼进正文的 @file:data:,... 长串）只服务 LLM，
// 不进用户可见正文；逐行剔除而非整段正则，避免误伤正文里的普通 @ 提及。
function stripAttachmentDirectives(text: string): string {
  return text
    .split('\n')
    .filter(line => !/^@file:/i.test(line.trim()))
    .join('\n')
}

function MessageBubbleInner({ message, variant }: MessageBubbleProps): React.JSX.Element {
  // 仅订阅本 id 的 body，避免流式增量触发全局重渲染。
  const bodies = useStore($chatMessageBodies, { keys: [message.id], deps: [message.id] })
  // 撤回在 in-flight 时会被服务端拒绝，必须订这个 atom，否则 memo 挡掉按钮显隐。
  const turnInFlight = useStore($chatTurnInFlight)
  const body: ChatMessageBody | undefined = bodies[message.id]

  if (!body) {
    return <></>
  }

  return (
    <MessageBubbleWithBody body={body} message={message} turnInFlight={turnInFlight} variant={variant ?? 'living'} />
  )
}

function formatBubbleTime(timestamp?: number): string {
  if (!timestamp) {
    return ''
  }

  const date = new Date(timestamp > 1e11 ? timestamp : timestamp * 1000)

  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function MessageBubbleWithBody({
  body,
  message,
  turnInFlight,
  variant
}: {
  body: ChatMessageBody
  message: ChatMessageListItem
  turnInFlight: boolean
  variant: ConversationVariant
}): React.JSX.Element {
  const subtype = message.subtype || ''
  const isUser = message.role === 'user'
  const portraitUrl = useStore($portraitUrl)

  // 压缩卡片折叠态：组件局部 useState，不持久化、不入 store；多窗口各自独立展开。
  const [compressExpanded, setCompressExpanded] = useState(false)

  if (COMPRESS_CARD_SUBTYPES.has(subtype)) {
    // 解析 content：第一行 "[🗜️ 对话压缩 — N 条早期消息已压缩]" 是胶囊标题，剩余为摘要 body。
    const rawText = body.text
    const newlineIdx = rawText.indexOf('\n')
    const title = newlineIdx === -1 ? rawText : rawText.slice(0, newlineIdx)
    const summary = newlineIdx === -1 ? '' : rawText.slice(newlineIdx + 1)
    const cardId = `compress-card-${message.id}`

    return (
      <div className="relative my-3 flex items-center gap-3 px-1">
        <div className="h-px flex-1 bg-line-strong" />
        <button
          aria-controls={cardId}
          aria-expanded={compressExpanded}
          className={cn(
            'group inline-flex max-w-[60%] items-center gap-1.5 truncate rounded-full border border-line-standard bg-surface-card/80 px-3 py-1 text-xs text-muted backdrop-blur-glass transition hover:bg-fill-hover hover:text-strong',
            'animate-in fade-in zoom-in-95 duration-150'
          )}
          onClick={() => setCompressExpanded(o => !o)}
          type="button"
        >
          <span className="truncate">{title}</span>
          <ChevronDown className={cn('size-3 shrink-0 transition-transform', compressExpanded && 'rotate-180')} />
        </button>
        <div className="h-px flex-1 bg-line-strong" />
        {compressExpanded && (
          <div
            className="absolute left-1/2 top-full z-10 mt-2 w-[min(560px,calc(100%-2rem))] -translate-x-1/2 rounded-2xl border border-line-standard bg-surface-card/95 p-3.5 text-[13px] leading-relaxed text-body shadow-lg backdrop-blur-glass"
            id={cardId}
          >
            <div className="whitespace-pre-wrap break-words">
              {summary || <span className="text-faint">（无摘要内容）</span>}
            </div>
          </div>
        )}
      </div>
    )
  }

  if (SYSTEM_PILL_SUBTYPES.has(subtype)) {
    return (
      <div className="my-1.5 flex justify-center px-2">
        <div className="max-w-[90%] rounded-full border border-line-standard bg-surface-card/60 px-3 py-1 text-center text-xs leading-relaxed text-muted backdrop-blur-glass shadow-xs">
          {body.text}
        </div>
      </div>
    )
  }

  if (STATUS_TRACE_SUBTYPES.has(subtype)) {
    return (
      <div className={`my-0.5 flex ${isUser ? 'justify-end' : 'justify-start'} px-2`}>
        <div className="max-w-[80%] text-[11px] italic text-faint">{body.text}</div>
      </div>
    )
  }

  if (subtype === AFFECT_TRACE_SUBTYPE) {
    return (
      <div className="my-0.5 flex justify-start px-2">
        <div className="max-w-[80%] text-[11px] italic text-faint">用表情/动作回应了</div>
      </div>
    )
  }

  if (subtype === MEDIA_STATUS_SUBTYPE) {
    return (
      <div className="my-1 flex justify-start px-2">
        <div className="flex max-w-[80%] flex-col gap-1">
          {body.media?.map(m => (
            <ChatMediaCard item={m} key={m.url} />
          ))}
        </div>
      </div>
    )
  }

  // 仅在生活空间、已完成、非错误、非取消且有文本的助手消息上显示播放按钮。工作台侧重干活直接看文本。
  const showPlayButton =
    variant === 'living' &&
    !isUser &&
    !body.streaming &&
    Boolean(body.text) &&
    !body.error &&
    !body.cancelled &&
    !body.toolName

  // 必须有后端 Message.id 才能回传；回合进行中服务端会拒绝撤回，按钮一并藏掉。
  const canOperate =
    Boolean(message.backendMessageId) &&
    !turnInFlight &&
    !body.streaming &&
    !body.error &&
    !body.cancelled &&
    !body.toolName

  // 生活空间只有唯一陪伴上下文，不允许派生。
  const canFork = variant === 'workbench' && canOperate

  // 避免误点助手行变成撤回伙伴上一句。
  const canUndo = isUser && canOperate

  // 用户附件渲染为可点击图片卡（data URL 或本地路径，媒体源通道负责取图）；
  // 正文剔除 @file: 指令行，纯图片消息不渲染空气泡。
  const visibleText = isUser ? stripAttachmentDirectives(body.text) : body.text
  // 规整展示文本：流式追加时去除前导空行防撑大气泡上方，非流式时去除首尾多余空白，保留内部段落与换行。
  const displayText = body.streaming ? visibleText.trimStart() : visibleText.trim()
  const hideTextBubble = isUser && !displayText && Boolean(body.attachments?.length)
  const tools = body.tools?.length ? body.tools : body.toolName ? [body.toolName] : []
  const toolOnly = tools.length > 0 && !displayText && !body.error && !body.cancelled
  const showToolIndicator = !isUser && tools.length > 0

  // 纯工具中间帧在生活空间不占位渲染
  if (variant === 'living' && toolOnly) {
    return <></>
  }

  // 非流式、非错误且无任何可见文本与媒体的空消息不渲染
  if (
    !isUser &&
    !displayText &&
    !body.streaming &&
    !body.attachments?.length &&
    !body.media?.length &&
    !body.error &&
    !body.cancelled
  ) {
    return <></>
  }

  // 只要消息具有非空可见正文且非流式传输中，即允许一键复制
  const canCopy = Boolean(displayText) && !body.streaming
  const hasActions = canFork || canUndo || canCopy

  return (
    <div className={cn('relative flex shrink-0 gap-2.5 overflow-visible', isUser ? 'justify-end' : 'justify-start')}>
      {!isUser && variant === 'workbench' && (
        <div className="size-8 shrink-0 overflow-hidden rounded-full border border-white/15 bg-white/10 shadow-sm mt-0.5">
          {portraitUrl ? (
            <img alt="Companion" className="size-full object-cover" src={portraitUrl} />
          ) : (
            <div className="flex size-full items-center justify-center bg-gradient-to-tr from-blue-600 to-indigo-500 text-[11px] font-bold text-white">
              S
            </div>
          )}
        </div>
      )}
      <div
        className={cn(
          'group/message relative flex max-w-[80%] items-center gap-1.5 overflow-visible',
          isUser ? 'flex-row-reverse' : 'flex-row'
        )}
      >
        <div className={cn('flex min-w-0 flex-col', isUser ? 'items-end' : 'items-start')}>
          {body.attachments?.length ? (
            <div className="flex flex-col gap-1">
              {body.attachments.map(a => (
                <ChatMediaCard item={{ type: a.type, url: a.url }} key={a.url} />
              ))}
            </div>
          ) : null}
          {showToolIndicator && variant === 'workbench' ? <ToolChipTimeline active={toolOnly} tools={tools} /> : null}
          {!hideTextBubble && !toolOnly ? (
            <div
              className={cn(
                'relative select-text cursor-text whitespace-pre-wrap break-words rounded-2xl px-4 py-2.5 text-xs leading-relaxed shadow-sm backdrop-blur-md',
                isUser
                  ? variant === 'workbench'
                    ? 'border border-blue-400/35 bg-blue-950/60 text-white shadow-[0_4px_16px_rgba(20,35,70,0.35),inset_0_1px_0_rgba(255,255,255,0.18)]'
                    : 'border border-accent-line/40 text-strong'
                  : variant === 'workbench'
                    ? 'border border-white/10 bg-white/[0.05] text-white/95 shadow-[0_4px_16px_rgba(0,0,0,0.25),inset_0_1px_0_rgba(255,255,255,0.12)]'
                    : 'border border-line-hairline bg-surface-card/20 text-strong'
              )}
              style={
                isUser && variant === 'living'
                  ? { backgroundColor: 'color-mix(in srgb, var(--ui-accent) 14%, transparent)' }
                  : undefined
              }
            >
              {body.error ? (
                <span className="text-amber-500">{body.error}</span>
              ) : body.cancelled ? (
                <span className="text-muted">已停止</span>
              ) : displayText ? (
                <>
                  {displayText}
                  {body.streaming && <span className="animate-caret-pulse" />}
                  {showPlayButton && (
                    <span className="ml-1.5 inline-flex align-middle select-none">
                      <ChatMessagePlayButton messageId={message.id} text={body.text} />
                    </span>
                  )}
                </>
              ) : (
                <span className="animate-pulse text-faint">…</span>
              )}
              {variant === 'workbench' && message.timestamp ? (
                <div
                  className={cn(
                    'mt-1.5 flex items-center gap-1 text-[10px] select-none',
                    isUser ? 'justify-end text-blue-200/50' : 'justify-end text-white/35'
                  )}
                >
                  <span>{formatBubbleTime(message.timestamp)}</span>
                </div>
              ) : null}
            </div>
          ) : null}
          {body.media?.length ? (
            <div className="mt-1 flex flex-col gap-1">
              {body.media.map(m => (
                <ChatMediaCard item={m} key={m.url} />
              ))}
            </div>
          ) : null}
        </div>
        {hasActions && (
          <MessageActionCluster
            canCopy={canCopy}
            canFork={canFork}
            canUndo={canUndo}
            copyText={displayText}
            messageId={message.id}
            sourceMessageId={message.backendMessageId}
            variant={variant}
          />
        )}
      </div>
    </div>
  )
}

function MessageActionCluster({
  canCopy,
  canFork,
  canUndo,
  copyText,
  messageId,
  sourceMessageId,
  variant
}: {
  canCopy: boolean
  canFork: boolean
  canUndo: boolean
  copyText?: string
  messageId: string
  sourceMessageId?: number
  variant: ConversationVariant
}): React.JSX.Element {
  return (
    <div
      className={cn(
        'flex shrink-0 items-center gap-0.5 rounded-full border border-line-hairline/80 bg-surface-card/90 p-0.5 shadow-sm backdrop-blur-md',
        'transition-opacity duration-150 select-none',
        'focus-within:pointer-events-auto focus-within:opacity-100',
        'has-[[data-busy]]:pointer-events-auto has-[[data-busy]]:opacity-100',
        variant === 'workbench'
          ? 'pointer-events-auto opacity-70 group-hover/message:opacity-100'
          : 'pointer-events-none opacity-0 group-hover/message:pointer-events-auto group-hover/message:opacity-100'
      )}
    >
      {canCopy && copyText && <ChatMessageCopyButton text={copyText} />}
      {canFork && sourceMessageId !== undefined && (
        <ChatMessageForkButton messageId={messageId} sourceMessageId={sourceMessageId} />
      )}
      {canUndo && sourceMessageId !== undefined && (
        <ChatMessageUndoButton messageId={messageId} sourceMessageId={sourceMessageId} />
      )}
    </div>
  )
}

// React.memo 保证历史气泡不会随 ChatDock 的内部状态变化重渲染。
export const MessageBubble = memo(MessageBubbleInner)
