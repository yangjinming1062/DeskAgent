import { useStore } from '@nanostores/react'
import { memo } from 'react'

import { ChatMediaCard } from './chat-media-card'
import { ChatMessagePlayButton } from './chat-message-play-button'
import { $chatMessageBodies } from './chat-store'
import type { ChatMessageBody, ChatMessageListItem } from './chat-store'

// 居中的元信息行，而非聊天气泡。
const SYSTEM_PILL_SUBTYPES = new Set(['hint', 'tool_summary', 'daily_summary', 'compress_summary'])
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
            className={`whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2 text-sm leading-relaxed ${
              isUser
                ? 'rounded-br-sm bg-accent text-white'
                : 'rounded-bl-sm border border-white/8 bg-surface-card text-white/90'
            }`}
          >
            {body.error ? (
              <span className="text-amber-300/90">{body.error}</span>
            ) : body.cancelled ? (
              <span className="text-white/50">已停止</span>
            ) : body.toolName ? (
              <span className="text-white/60">正在使用 {body.toolName}…</span>
            ) : visibleText ? (
              visibleText
            ) : (
              '…'
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
        {showPlayButton && <ChatMessagePlayButton className="mt-1" messageId={message.id} text={body.text} />}
      </div>
    </div>
  )
}

// React.memo 保证历史气泡不会随 ChatDock 的内部状态变化重渲染。
export const MessageBubble = memo(MessageBubbleInner)
