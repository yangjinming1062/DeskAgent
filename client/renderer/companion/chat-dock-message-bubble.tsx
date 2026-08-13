import type { ChatMessage } from './chat-store'

// Centered meta line rather than a chat bubble.
const SYSTEM_PILL_SUBTYPES = new Set(['hint', 'tool_summary', 'daily_summary', 'compress_summary'])
// Poke/drag traces: side-aligned but visually recessive.
const STATUS_TRACE_SUBTYPES = new Set(['status_interaction', 'status_reaction'])

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

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[80%] whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2 text-sm leading-relaxed ${
          isUser ? 'rounded-br-sm bg-(--theme-primary, #6c8aff) text-white' : 'rounded-bl-sm bg-white/10 text-white/90'
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
    </div>
  )
}
