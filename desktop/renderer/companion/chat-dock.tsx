import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'

import { useGatewayRequest } from '@/companion/boot/use-gateway-request'
import {
  $chatMessages,
  $chatSessionId,
  type ChatMessage,
  pushUserMessage,
  setAssistantError,
  setChatSession
} from '@/companion/chat-store'
import { $disturbanceTier, type DisturbanceTier, setDisturbanceTier, setSpriteState } from '@/companion/companion-store'
import { $gatewayState } from '@/shared/store/gateway'

const TIERS: { id: DisturbanceTier; label: string }[] = [
  { id: 'proactive', label: '积极' },
  { id: 'normal', label: '常规' },
  { id: 'quiet', label: '安静' }
]

interface ChatDockProps {
  onClose: () => void
}

export function ChatDock({ onClose }: ChatDockProps) {
  const messages = useStore($chatMessages)
  const sessionId = useStore($chatSessionId)
  const gatewayState = useStore($gatewayState)
  const tier = useStore($disturbanceTier)
  const { requestGateway } = useGatewayRequest()
  const [text, setText] = useState('')
  const [pendingImage, setPendingImage] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  // Chat is a focused surface — capture mouse across the window and drop
  // always-on-top so other apps can cover it while the user types. Restore on
  // unmount (close).
  useEffect(() => {
    void window.deskagent.sprite.setIgnoreMouseEvents({ ignore: false })
    void window.deskagent.sprite.setAlwaysOnTop({ on: false })
    inputRef.current?.focus()

    return () => {
      void window.deskagent.sprite.setAlwaysOnTop({ on: true })
      void window.deskagent.sprite.setIgnoreMouseEvents({ ignore: true, forward: true })
    }
  }, [])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

  const ensureSession = async (): Promise<string> => {
    const existing = $chatSessionId.get()

    if (existing) {return existing}
    const res = await requestGateway<{ session_id: string }>('session.create', {})
    setChatSession(res.session_id)

    return res.session_id
  }

  const changeTier = (next: DisturbanceTier) => {
    setDisturbanceTier(next)
    // Report to Backend (companion.set_disturbance_tier). No-op until the
    // Backend endpoint ships — never block the UI on the report.
    void requestGateway('companion.set_disturbance_tier', { tier: next }).catch(() => {})
  }

  const onPaste = async (e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items

    if (!items) {return}

    for (const item of items) {
      if (item.type.startsWith('image/')) {
        e.preventDefault()

        try {
          const path = await window.deskagent.saveClipboardImage()

          if (path) {setPendingImage(path)}
        } catch {
          /* ignore clipboard read failure */
        }

        return
      }
    }
  }

  const send = async () => {
    const trimmed = text.trim()

    if ((!trimmed && !pendingImage) || sending) {return}

    if (gatewayState !== 'open') {return}

    setSending(true)

    try {
      const id = await ensureSession()
      let fullText = trimmed
      const attachments: string[] = []

      if (pendingImage) {
        const ref = await requestGateway<{ ref_text?: string }>('image.attach', {
          session_id: id,
          path: pendingImage
        })

        if (ref.ref_text) {
          fullText = `${fullText}\n${ref.ref_text}`.trim()
          attachments.push(pendingImage)
        }
      }

      pushUserMessage(fullText || '（图片）', attachments.length ? attachments : undefined)
      setSpriteState('thinking')
      setText('')
      setPendingImage(null)
      await requestGateway('prompt.submit', { session_id: id, text: fullText || '请看这张图' })
    } catch (err) {
      setAssistantError(err instanceof Error ? err.message : '发送失败')
      setSpriteState('idle')
    } finally {
      setSending(false)
    }
  }

  // Show a typing indicator while the user's message has no streaming
  // assistant reply yet (between prompt.submit returning {queued} and
  // message.start). Once message.start pushes a streaming assistant bubble it
  // renders its own "…".
  const lastIsUser = messages[messages.length - 1]?.role === 'user'
  const showTyping = lastIsUser && gatewayState === 'open'

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center px-6" style={{ pointerEvents: 'auto' }}>
      <div className="flex h-[min(70vh,560px)] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-white/10 bg-black/55 text-white shadow-2xl backdrop-blur-md">
        <div className="flex items-center justify-between gap-2 border-b border-white/10 px-3 py-2">
          <div className="flex items-center gap-0.5 rounded-full bg-white/5 p-0.5 text-[11px]" title="打扰档位">
            {TIERS.map(t => (
              <button
                className={`rounded-full px-2.5 py-1 transition ${tier === t.id ? 'bg-white/80 font-medium text-black' : 'text-white/60 hover:text-white'}`}
                key={t.id}
                onClick={() => changeTier(t.id)}
                type="button"
              >
                {t.label}
              </button>
            ))}
          </div>
          <button aria-label="关闭对话" className="text-white/50 transition hover:text-white" onClick={onClose} type="button">
            ✕
          </button>
        </div>

        <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4" ref={scrollRef}>
          {messages.length === 0 && !sending && (
            <p className="mt-6 text-center text-sm text-white/40">说点什么，或粘贴一张图给我看看～</p>
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

        <div className="border-t border-white/10 p-3">
          {gatewayState !== 'open' && (
            <p className="mb-2 text-center text-xs text-amber-300/70">正在连接…</p>
          )}
          <div className="flex items-end gap-2">
            <textarea
              className="max-h-32 flex-1 resize-none rounded-lg border border-white/15 bg-white/10 px-3 py-2 text-sm outline-none placeholder:text-white/40 focus:border-white/40"
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
              className="rounded-lg bg-white/90 px-4 py-2 text-sm font-medium text-black transition hover:bg-white disabled:opacity-40"
              disabled={sending || gatewayState !== 'open' || (!text.trim() && !pendingImage)}
              onClick={() => void send()}
              type="button"
            >
              发送
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user'

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[80%] whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2 text-sm leading-relaxed ${
          isUser ? 'rounded-br-sm bg-(--theme-primary, #6c8aff) text-white' : 'rounded-bl-sm bg-white/10 text-white/90'
        }`}
      >
        {message.error ? (
          <span className="text-amber-300/90">😬 {message.error}</span>
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
