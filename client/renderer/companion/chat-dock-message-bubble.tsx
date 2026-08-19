import { ChatMessagePlayButton } from './chat-message-play-button'
import type { ChatMessage } from './chat-store'

// 居中的元信息行，而非聊天气泡。
const SYSTEM_PILL_SUBTYPES = new Set(['hint', 'tool_summary', 'daily_summary', 'compress_summary'])
// 戳 / 拖拽追踪：侧对齐但视觉上弱化。
const STATUS_TRACE_SUBTYPES = new Set(['status_interaction', 'status_reaction'])
// 仅肢体语言回复（affect / action，无文字）：持久化以保留完整的 LLM 上下文，
// 但渲染为低权重 trace——3D 已经现场表达过，
// 文字记录只需标注"伙伴在这里做出了反应"。
const AFFECT_TRACE_SUBTYPE = 'status_affect'

export function MessageBubble({ message }: { message: ChatMessage }): React.JSX.Element {
  const subtype = message.subtype || ''
  const isUser = message.role === 'user'

  if (SYSTEM_PILL_SUBTYPES.has(subtype)) {
    return (
      <div className="my-1.5 flex justify-center px-2">
        <div className="max-w-[90%] rounded-full border border-white/10 bg-white/5 px-3 py-1 text-center text-xs leading-relaxed text-white/50">
          {message.text}
        </div>
      </div>
    )
  }

  if (STATUS_TRACE_SUBTYPES.has(subtype)) {
    return (
      <div className={`my-0.5 flex ${isUser ? 'justify-end' : 'justify-start'} px-2`}>
        <div className="max-w-[80%] text-[11px] italic text-white/40">{message.text}</div>
      </div>
    )
  }

  if (subtype === AFFECT_TRACE_SUBTYPE) {
    return (
      <div className="my-0.5 flex justify-start px-2">
        <div className="max-w-[80%] text-[11px] italic text-white/40">😶 用表情/动作回应了</div>
      </div>
    )
  }

  // 「播放」按钮只展示在已完成、可朗读、有真实文本的精灵回复上：
  //   - 非用户消息
  //   - 流式已结束（streaming=false）
  //   - 有 text 且非 error / cancelled / tool 占位
  const showPlayButton =
    !isUser && !message.streaming && Boolean(message.text) && !message.error && !message.cancelled && !message.toolName

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div className={`flex max-w-[80%] flex-col ${isUser ? 'items-end' : 'items-start'}`}>
        <div
          className={`whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2 text-sm leading-relaxed ${
            isUser
              ? 'rounded-br-sm bg-(--theme-primary, #6c8aff) text-white'
              : 'rounded-bl-sm bg-white/10 text-white/90'
          }`}
        >
          {message.error ? (
            <span className="text-amber-300/90">😬 {message.error}</span>
          ) : message.cancelled ? (
            <span className="text-white/50">已停止</span>
          ) : message.toolName ? (
            <span className="text-white/60">🔧 正在使用 {message.toolName}…</span>
          ) : message.text ? (
            message.text
          ) : (
            '…'
          )}
        </div>
        {showPlayButton && <ChatMessagePlayButton className="mt-1" messageId={message.id} text={message.text} />}
      </div>
    </div>
  )
}
