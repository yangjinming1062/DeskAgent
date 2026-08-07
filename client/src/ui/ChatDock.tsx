import { useEffect, useRef, useState } from 'react'
import { useStore } from '@nanostores/react'
import { addUserMessage, $assistantMessageId, $messages, $sessionId, $streamingText } from '../state/chat-store'
import { gateway } from '../state/gateway-store'

export function ChatDock(): React.JSX.Element {
  const messages = useStore($messages)
  const streamingId = useStore($assistantMessageId)
  const streamingText = useStore($streamingText)
  const [input, setInput] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, streamingText])

  const send = async (): Promise<void> => {
    const text = input.trim()
    if (!text) return
    setInput('')
    addUserMessage(text)
    try {
      let sid = $sessionId.get()
      if (!sid) {
        const result = await gateway.request<{ session_id: string }>('session.create', {})
        sid = result.session_id
        $sessionId.set(sid)
      }
      await gateway.request('prompt.submit', { session_id: sid, text })
    } catch (err) {
      console.error('Send failed:', err)
    }
  }

  return (
    <div className="chat-dock">
      <div className="chat-messages" ref={scrollRef}>
        {messages.length === 0 && <div className="chat-empty">点击角色开始对话</div>}
        {messages.map(m => {
          // Merge in-progress streaming text for the active assistant message
          const text = m.id === streamingId && m.pending && streamingText ? streamingText : m.text
          return (
            <div key={m.id} className={`msg ${m.role}`}>
              {text || (m.pending ? '…' : '')}
            </div>
          )
        })}
      </div>
      <div className="chat-input-row">
        <input
          className="chat-input"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send() } }}
          placeholder="输入消息…"
        />
        <button className="chat-send" onClick={() => void send()} disabled={!input.trim()}>
          发送
        </button>
      </div>
    </div>
  )
}
