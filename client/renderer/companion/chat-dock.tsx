import { useStore } from '@nanostores/react'
import type React from 'react'
import { type PointerEvent as ReactPointerEvent, useEffect, useMemo, useRef, useState } from 'react'

import { useGatewayRequest } from '@/companion/boot/use-gateway-request'
import {
  $chatMessages,
  $chatSessionId,
  type ChatMessage,
  finalizeAssistantMessage,
  pushUserMessage,
  setAssistantCancelled,
  setAssistantError,
  setChatSession
} from '@/companion/chat-store'
import {
  $effectiveTier,
  $userPreferredTier,
  type DisturbanceTier,
  setDisturbanceTier,
  setSpriteState
} from '@/companion/companion-store'
import { useInteractiveRegion } from '@/companion/interactive-regions'
import { $portraitUrl } from '@/companion/portrait-store'
import { $spatialPos, $spatialScale, $viewport, computeOverlayAnchorBesideSprite } from '@/companion/spatial'
import { getDeskAgentConfig } from '@/shared/deskagent/config'
import { $gatewayState } from '@/shared/store/gateway'

import { DISTURBANCE_TIERS } from './disturbance-tiers'

// matches the panel's max-w-lg (32rem) so we can position it before mount
const DOCK_MAX_W = 512
const DOCK_MAX_H = 520
const DOCK_GAP = 16
const DOCK_HEAD_OFFSET_RATIO = 0.1

interface ChatDockProps {
  onClose: () => void
  onOpenVoiceCall?: () => void
}

export function ChatDock({ onClose, onOpenVoiceCall }: ChatDockProps): React.ReactElement {
  const messages = useStore($chatMessages)
  const sessionId = useStore($chatSessionId)
  const gatewayState = useStore($gatewayState)
  const tier = useStore($userPreferredTier)
  const portraitUrl = useStore($portraitUrl)
  const { requestGateway } = useGatewayRequest()
  const [text, setText] = useState('')
  const [pendingImage, setPendingImage] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [recording, setRecording] = useState(false)
  const [config, setConfig] = useState<{ voice?: { max_recording_seconds?: number } }>({})
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const voiceChunksRef = useRef<Blob[]>([])
  const autoStopTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // The chat panel inherits the sprite window's topmost flag — no toggling;
  // an unmount-time toggle would race the closing dock.
  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  // Reset the sprite on unmount (force: true so the priority gate can't
  // swallow it while 'listening'/'thinking') — a dismissed chat with an
  // in-flight recording would otherwise leave the sprite stuck there.
  // Skip the reset when an assistant turn is still streaming: forcing idle
  // here would clobber the thinking-state animation that's narrating the
  // user's request they just sent (ARCH §2.3 — lower priority states can't
  // interrupt higher ones without an explicit force).
  useEffect(() => {
    return () => {
      const recorder = mediaRecorderRef.current

      if (recorder && recorder.state !== 'inactive') {
        try {
          recorder.stop()
        } catch {
          /* already stopped */
        }
      }

      if (autoStopTimeoutRef.current) {
        clearTimeout(autoStopTimeoutRef.current)
        autoStopTimeoutRef.current = null
      }

      setRecording(false)

      const inFlight = $chatMessages.get().some(m => m.streaming)

      if (!inFlight) {
        setSpriteState('idle', { force: true })
      }
    }
  }, [])

  const panelRef = useRef<HTMLDivElement>(null)
  useInteractiveRegion('chat-dock', panelRef)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

  // Load the persisted cap for voice recordings. Cheap GET — only rerun
  // when the dock mounts; settings changes while recording won't snap the
  // current recording to a new cap.
  useEffect(() => {
    void getDeskAgentConfig()
      .then(c => setConfig({ voice: c.voice }))
      .catch(() => setConfig({}))
  }, [])

  const ensureSession = async (): Promise<string> => {
    const existing = $chatSessionId.get()

    if (existing) {
      return existing
    }

    const res = await requestGateway<{ session_id: string }>('session.create', {})
    setChatSession(res.session_id)

    return res.session_id
  }

  const changeTier = (next: DisturbanceTier) => {
    setDisturbanceTier(next)
    // Push the EFFECTIVE tier (which incorporates both user choice and the
    // activity-monitor override) so the backend gates on the same value the
    // renderer reads. Without this, an immersive IDE context would un-mute
    // the backend for the full poll-cycle window after a manual click.
    void requestGateway('companion.set_disturbance_tier', { tier: $effectiveTier.get() }).catch(() => {})
  }

  const onPaste = async (e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items

    if (!items) {
      return
    }

    for (const item of items) {
      if (item.type.startsWith('image/')) {
        e.preventDefault()

        try {
          const path = await window.deskagent.saveClipboardImage()

          if (path) {
            setPendingImage(path)
          }
        } catch {
          /* ignore clipboard read failure */
        }

        return
      }
    }
  }

  // stopRecording waits for an in-flight getUserMedia so a quick tap
  // doesn't land in stopRecording before the recorder exists.
  const startPendingRef = useRef<Promise<void> | null>(null)

  // Latest-callback ref so the global-mouseup effect below can subscribe
  // without depending on stopRecording's identity (which changes every render
  // and would otherwise re-subscribe the listener on every keystroke).
  const stopRecordingRef = useRef<() => Promise<void>>(async () => {})

  const startRecording = () => {
    let pending: Promise<void> | null = null
    pending = (async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
        const recorder = new MediaRecorder(stream)
        mediaRecorderRef.current = recorder
        voiceChunksRef.current = []
        autoStopTimeoutRef.current = null

        recorder.ondataavailable = e => {
          if (e.data.size > 0) {
            voiceChunksRef.current.push(e.data)
          }
        }

        setRecording(true)
        setSpriteState('listening')
        recorder.start()

        // Honor the voice.max_recording_seconds cap. The renderer is the
        // authority on recording length because the audio stream lives in
        // Chromium; the backend can't stop it mid-capture.
        const cap = config.voice?.max_recording_seconds ?? 60

        if (cap > 0) {
          autoStopTimeoutRef.current = setTimeout(() => {
            if (mediaRecorderRef.current?.state === 'recording') {
              void stopRecording()
            }
          }, cap * 1000)
        }
      } catch {
        setAssistantError('无法使用麦克风录制语音')
      } finally {
        if (startPendingRef.current === pending) {
          startPendingRef.current = null
        }
      }
    })()
    startPendingRef.current = pending
  }

  const stopRecording = async () => {
    // Wait for the start promise so the recorder exists before deciding what to do.
    if (startPendingRef.current) {
      try {
        await startPendingRef.current
      } catch {
        /* surfaced via startRecording */
      }
    }

    // Clear the auto-stop timer — manual stop fires before the cap.
    if (autoStopTimeoutRef.current) {
      clearTimeout(autoStopTimeoutRef.current)
      autoStopTimeoutRef.current = null
    }

    const recorder = mediaRecorderRef.current

    if (!recorder || recorder.state === 'inactive') {
      setRecording(false)

      return
    }

    recorder.onstop = () => {
      recorder.stream.getTracks().forEach(t => t.stop())
      void transcribeAndSend()
    }

    recorder.stop()
    setRecording(false)
  }

  // Keep the ref pointing at the latest closure so the global mouseup
  // handler always sees fresh state.
  stopRecordingRef.current = stopRecording

  useEffect(() => {
    if (!recording) {
      return
    }

    const handleGlobalMouseUp = () => {
      void stopRecordingRef.current()
    }

    window.addEventListener('mouseup', handleGlobalMouseUp)

    return () => {
      window.removeEventListener('mouseup', handleGlobalMouseUp)
    }
  }, [recording])

  // Push-to-talk voice message: record → cloud STT (media.stt) → send the
  // transcribed text as a normal prompt. Falls back to a typed hint when STT
  // is unavailable so the user is never stuck (plan §5 always-fallback-text).
  const transcribeAndSend = async () => {
    setSpriteState('thinking')
    const blob = new Blob(voiceChunksRef.current, { type: 'audio/webm' })
    voiceChunksRef.current = []
    let text = ''

    try {
      const reader = new FileReader()

      const dataUrl: string = await new Promise((resolve, reject) => {
        reader.onload = () => resolve(reader.result as string)
        reader.onerror = () => reject(new Error('read failed'))
        reader.readAsDataURL(blob)
      })

      const res = await window.deskagent.media.stt({ dataUrl, filename: 'voice.webm' })
      text = (res.text ?? '').trim()
    } catch {
      setAssistantError('没听清，用打字吧～')
      setSpriteState('idle')

      return
    }

    if (!text) {
      setAssistantError('没听清，用打字吧～')
      setSpriteState('idle')

      return
    }

    pushUserMessage(text)

    try {
      const id = await ensureSession()
      await requestGateway('prompt.submit', { session_id: id, text })
    } catch (err) {
      setAssistantError(err instanceof Error ? err.message : '发送失败')
      setSpriteState('idle')
    }
  }

  const send = async () => {
    const trimmed = text.trim()

    if ((!trimmed && !pendingImage) || sending) {
      return
    }

    if (gatewayState !== 'open') {
      return
    }

    setSending(true)

    try {
      const id = await ensureSession()
      let fullText = trimmed
      const attachments: string[] = []

      if (pendingImage) {
        // Convert the local path to a data URL — the backend can't read
        // the path in Docker/remote deployments and would ignore the image.
        let attachmentUrl = pendingImage

        try {
          if (!pendingImage.startsWith('data:')) {
            const dataUrl = await window.deskagent.readFileDataUrl(pendingImage)

            if (dataUrl) {
              attachmentUrl = dataUrl
            }
          }
        } catch {
          /* keep the local path; backend may still resolve it via volume mount */
        }

        const ref = await requestGateway<{ ref_text?: string }>('image.attach', {
          session_id: id,
          path: attachmentUrl
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
      await requestGateway('prompt.submit', {
        session_id: id,
        text: fullText || '请看这张图',
        // Forward attachments as {file_url, type} so the LLM sees the image.
        ...(attachments.length ? { attachments: attachments.map(file_url => ({ file_url, type: 'image' })) } : {})
      })
    } catch (err) {
      setAssistantError(err instanceof Error ? err.message : '发送失败')
      setSpriteState('idle')
      setPendingImage(null)
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

  // Generating covers both the pre-response gap (showTyping) and the streaming
  // phase (last assistant bubble still streaming). Either way the user should
  // be able to interrupt.
  const lastMsg = messages[messages.length - 1]

  const isGenerating =
    gatewayState === 'open' && (showTyping || (lastMsg?.role === 'assistant' && lastMsg.streaming === true))

  const handleStop = async () => {
    const sid = $chatSessionId.get()

    if (sid) {
      try {
        await requestGateway('session.interrupt', { session_id: sid })
      } catch {
        /* best effort — local finalize below covers the UX */
      }
    }

    // Backend cancellation aborts run_chat_turn mid-stream — no message.complete
    // arrives, so we must finalize the streaming bubble locally to avoid a
    // stuck "…" indicator. If the user clicked Stop during the pre-response
    // window (last message is still the user one), append a cancelled
    // assistant placeholder so `showTyping` clears and Send comes back.
    const last = $chatMessages.get().at(-1)

    if (last?.role === 'assistant' && last.streaming) {
      finalizeAssistantMessage()
    } else {
      // User-initiated stop before the first chunk arrived — render a
      // neutral cancelled placeholder (not the 😬 error bubble).
      setAssistantCancelled()
    }

    setSpriteState('idle', { force: true })
  }

  // Drag the panel via translate3d (GPU motion, no re-render per pointermove)
  // and persist the offset so the choice survives a restart.
  const pos = useStore($spatialPos)
  const scale = useStore($spatialScale)
  const viewport = useStore($viewport)

  const { left: baseLeft, top: baseTop } = computeOverlayAnchorBesideSprite({
    pos,
    scale,
    gap: DOCK_GAP,
    overlayMaxW: DOCK_MAX_W,
    overlayH: DOCK_MAX_H,
    vw: viewport.width,
    vh: viewport.height,
    verticalRatio: DOCK_HEAD_OFFSET_RATIO
  })

  const storedOffset = useMemo(() => {
    if (typeof localStorage === 'undefined') {
      return null
    }

    try {
      const raw = localStorage.getItem('da.companion.chatDockOffset')

      return raw ? (JSON.parse(raw) as { dx: number; dy: number }) : null
    } catch {
      return null
    }
  }, [])

  const offsetRef = useRef<{ dx: number; dy: number }>(storedOffset ?? { dx: 0, dy: 0 })
  const dragRef = useRef<{ startX: number; startY: number; baseDx: number; baseDy: number } | null>(null)

  const onHeaderPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    // Only left-button drags; ignore middle/right click and modifier-hold.
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) {
      return
    }

    const target = e.target as HTMLElement

    // Don't start a drag when the user actually clicked a button / input
    // inside the header (tier pill, voice-call button, close).
    if (target.closest('button, input, textarea, select, a, [role="button"]')) {
      return
    }

    e.currentTarget.setPointerCapture(e.pointerId)
    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      baseDx: offsetRef.current.dx,
      baseDy: offsetRef.current.dy
    }
  }

  const onHeaderPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const d = dragRef.current

    if (!d) {
      return
    }

    const next = { dx: d.baseDx + (e.clientX - d.startX), dy: d.baseDy + (e.clientY - d.startY) }
    offsetRef.current = next

    if (panelRef.current) {
      panelRef.current.style.transform = `translate3d(${next.dx}px, ${next.dy}px, 0)`
    }
  }

  const onHeaderPointerUp = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) {
      return
    }

    e.currentTarget.releasePointerCapture(e.pointerId)
    dragRef.current = null

    if (typeof localStorage !== 'undefined') {
      try {
        localStorage.setItem('da.companion.chatDockOffset', JSON.stringify(offsetRef.current))
      } catch {
        /* private mode: in-memory only */
      }
    }
  }

  return (
    // Anchor the panel beside the sprite (SPEC §4.1 对话发生在角色身边) so it
    // follows wherever the user has dragged or routed the companion, rather
    // than pinning to the screen bottom and drifting away from the character.
    <div className="fixed inset-0 z-40 pointer-events-none">
      <div
        className="flex h-[min(60vh,520px)] max-w-lg flex-col overflow-hidden rounded-2xl border border-white/10 bg-black/55 text-white shadow-2xl backdrop-blur-md"
        ref={panelRef}
        style={{
          position: 'fixed',
          left: baseLeft,
          top: baseTop,
          width: 'min(calc(100vw - 2rem), 32rem)',
          pointerEvents: 'auto',
          transform: storedOffset ? `translate3d(${storedOffset.dx}px, ${storedOffset.dy}px, 0)` : undefined
        }}
      >
        <div
          className="flex cursor-grab items-center justify-between gap-2 border-b border-white/10 px-3 py-2 active:cursor-grabbing"
          onPointerCancel={onHeaderPointerUp}
          onPointerDown={onHeaderPointerDown}
          onPointerMove={onHeaderPointerMove}
          onPointerUp={onHeaderPointerUp}
          title="拖动以移动对话框"
        >
          <div className="flex items-center gap-2">
            {portraitUrl && (
              <img
                alt="伙伴形象"
                className="h-7 w-7 rounded-full border border-white/15 object-cover"
                src={portraitUrl}
              />
            )}
            <div className="flex items-center gap-0.5 rounded-full bg-white/5 p-0.5 text-[11px]" title="打扰档位">
              {DISTURBANCE_TIERS.map(t => (
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
            {onOpenVoiceCall && (
              <button
                className="rounded-full border border-white/20 bg-white/10 px-2.5 py-1 text-[11px] text-white/80 transition hover:bg-white/20"
                onClick={onOpenVoiceCall}
                title="开启语音通话模式"
                type="button"
              >
                📞 通话
              </button>
            )}
          </div>
          <button
            aria-label="关闭对话"
            className="text-white/50 transition hover:text-white"
            onClick={onClose}
            type="button"
          >
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
          {gatewayState !== 'open' && <p className="mb-2 text-center text-xs text-amber-300/70">正在连接…</p>}
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
              className={`rounded-lg border px-3 py-2 text-xs font-medium transition ${
                recording
                  ? 'border-red-400/80 bg-red-500/30 text-white animate-pulse'
                  : 'border-white/20 bg-white/5 text-white/70 hover:bg-white/15 hover:text-white'
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
              {recording ? '松开发送' : '🎤 语音'}
            </button>
            <button
              className="rounded-lg bg-white/90 px-4 py-2 text-sm font-medium text-black transition hover:bg-white disabled:opacity-40"
              disabled={!isGenerating && (sending || gatewayState !== 'open' || (!text.trim() && !pendingImage))}
              onClick={() => void (isGenerating ? handleStop() : send())}
              type="button"
            >
              {isGenerating ? '停止' : '发送'}
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
