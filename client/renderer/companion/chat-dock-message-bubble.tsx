import { useStore } from '@nanostores/react'
import { memo } from 'react'

import { ChatMediaCard } from './chat-media-card'
import { ChatMessageForkButton } from './chat-message-fork-button'
import { ChatMessagePlayButton } from './chat-message-play-button'
import { $chatMessageBodies } from './chat-store'
import type { ChatMessageBody, ChatMessageListItem } from './chat-store'

// 居中的元信息行，而非聊天气泡。Slash 命令结果与历史清空标记（详见 PROTOCOL §1.9）走同一形态。
const SYSTEM_PILL_SUBTYPES = new Set([
  'hint',
  'tool_summary',
  'daily_summary',
  'compress_summary',
  'status_cleared',
  'status_command_result'
])

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

function MessageBubbleInner({ message }: { message: ChatMessageListItem }): React.JSX.Element {
  const subtype = message.subtype || ''
  const isUser = message.role === 'user'

  // 仅订阅本 id 的 body，避免流式增量触发全局重渲染。
  const bodies = useStore($chatMessageBodies, { keys: [message.id], deps: [message.id] })
  const body: ChatMessageBody | undefined = bodies[message.id]

  if (!body) {
    return <></>
  }

  if (SYSTEM_PILL_SUBTYPES.has(subtype)) {
    return (
      <div className="my-1.5 flex justify-center px-2">
        <div className="max-w-[90%] rounded-full border border-white/10 bg-glass px-3 py-1 text-center text-xs leading-relaxed text-white/50 backdrop-blur-glass">
          {body.text}
        </div>
      </div>
    )
  }

  if (STATUS_TRACE_SUBTYPES.has(subtype)) {
    return (
      <div className={`my-0.5 flex ${isUser ? 'justify-end' : 'justify-start'} px-2`}>
        <div className="max-w-[80%] text-[11px] italic text-white/40">{body.text}</div>
      </div>
    )
  }

  if (subtype === AFFECT_TRACE_SUBTYPE) {
    return (
      <div className="my-0.5 flex justify-start px-2">
        <div className="max-w-[80%] text-[11px] italic text-white/40">用表情/动作回应了</div>
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

  // 仅在已完成、非错误、非取消且有文本的助手消息上显示播放按钮。
  const showPlayButton =
    !isUser && !body.streaming && Boolean(body.text) && !body.error && !body.cancelled && !body.toolName

  // 派生按钮：必须有后端 Message.id 才能回传给 session.fork；流式中/出错/已取消/正在调用工具时禁用避免歧义。
  const canFork =
    Boolean(message.backendMessageId) && !body.streaming && !body.error && !body.cancelled && !body.toolName

  // 用户附件渲染为可点击图片卡（data URL 或本地路径，媒体源通道负责取图）；
  // 正文剔除 @file: 指令行，纯图片消息不渲染空气泡。
  const visibleText = isUser ? stripAttachmentDirectives(body.text) : body.text
  const hideTextBubble = isUser && !visibleText.trim() && Boolean(body.attachments?.length)

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div className={`flex max-w-[80%] flex-col ${isUser ? 'items-end' : 'items-start'}`}>
        {body.attachments?.length ? (
          <div className="flex flex-col gap-1">
            {body.attachments.map(a => (
              <ChatMediaCard item={{ type: a.type, url: a.url }} key={a.url} />
            ))}
          </div>
        ) : null}
        {!hideTextBubble && (
          <div
            className={`relative whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed ${
              isUser
                ? 'rounded-br-sm bg-accent text-white shadow-md shadow-accent/15 border border-white/10'
                : 'rounded-bl-sm border border-white/10 bg-surface-card text-white/90 shadow-sm'
            }`}
          >
            {body.error ? (
              <span className="text-amber-300/90">{body.error}</span>
            ) : body.cancelled ? (
              <span className="text-white/50">已停止</span>
            ) : body.toolName ? (
              <span className="inline-flex items-center gap-1.5 font-mono text-xs text-accent">
                <span className="size-1.5 animate-ping rounded-full bg-accent" />
                <span>[EXEC: {body.toolName}]</span>
              </span>
            ) : visibleText ? (
              <>
                {visibleText}
                {body.streaming && <span className="animate-caret-pulse" />}
              </>
            ) : (
              <span className="animate-pulse text-white/40">…</span>
            )}
          </div>
        )}
        {body.media?.length ? (
          <div className="mt-1 flex flex-col gap-1">
            {body.media.map(m => (
              <ChatMediaCard item={m} key={m.url} />
            ))}
          </div>
        ) : null}
        {body.draft && (
          <span className="mt-1 inline-flex items-center gap-1 rounded-full border border-amber-300/30 bg-amber-300/10 px-2 py-0.5 text-[11px] text-amber-200/90 font-mono">
            [DRAFT: 未发送]
          </span>
        )}
        {showPlayButton && <ChatMessagePlayButton className="mt-1" messageId={message.id} text={body.text} />}
        {canFork && (
          <ChatMessageForkButton className="mt-1" messageId={message.id} sourceMessageId={message.backendMessageId!} />
        )}
      </div>
    </div>
  )
}

// React.memo 保证历史气泡不会随 ChatDock 的内部状态变化重渲染。
export const MessageBubble = memo(MessageBubbleInner)
