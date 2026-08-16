import { atom } from 'nanostores'

import { setSpriteState } from '@/companion/companion-store'
import { sleep } from '@/shared/lib/utils'
import { $gateway } from '@/shared/store/gateway'
import type { SessionMessage } from '@/shared/types/spiritagent'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  subtype?: string
  streaming?: boolean
  attachments?: string[]
  toolName?: string | null
  error?: string
  /** User-initiated stop (vs an error). Rendered neutrally, not as 😬. */
  cancelled?: boolean
}

export const $chatMessages = atom<ChatMessage[]>([])
export const $chatSessionId = atom<string | null>(null)
export const $chatOpen = atom(false)
// A transient proactive message the companion speaks aloud and surfaces as a
// bubble when the chat dock is closed. Cleared after the utterance ends.
export const $proactiveBubble = atom<string | null>(null)

let idCounter = 0
const nextId = () => `m${++idCounter}`

export function setChatOpen(open: boolean): void {
  $chatOpen.set(open)
}

export function setChatSession(id: string | null): void {
  clearPendingPrompts()
  $chatSessionId.set(id)
}

/** Replace the dock's transcript with a conversation loaded from the backend.
 * `system` / `tool` rows keep their subtype so the bubble renderer can pick the
 * right presentation; they are folded onto the assistant side for layout only. */
export function hydrateChatMessages(messages: SessionMessage[]): void {
  $chatMessages.set(
    messages.map(m => ({
      id: nextId(),
      role: m.role === 'user' ? 'user' : 'assistant',
      text: extractText(m),
      subtype: m.subtype,
      toolName: m.tool_name ?? null
    }))
  )
}

function extractText(m: SessionMessage): string {
  // ``SessionMessage.content`` is unknown — a multimodal_v1 user message
  // arrives as a JSON parts array; render only the user-visible text so the
  // bubble doesn't leak ``[{"type": "image_url", ...}]``.
  if (typeof m.content !== 'string') {
    return ''
  }

  let parsed: unknown

  try {
    parsed = JSON.parse(m.content)
  } catch {
    return m.content
  }

  if (!Array.isArray(parsed)) {
    return m.content
  }

  return parsed
    .filter((p): p is { type?: string; text?: string } => typeof p === 'object' && p !== null)
    .filter(p => p.type === 'text' && typeof p.text === 'string')
    .map(p => p.text as string)
    .join('\n')
}

export function setProactiveBubble(text: string | null): void {
  $proactiveBubble.set(text)
}

export function pushProactiveMessage(text: string): void {
  $chatMessages.set([...$chatMessages.get(), { id: nextId(), role: 'assistant', text, subtype: 'status_proactive' }])
}

export function pushUserMessage(text: string, attachments?: string[]): string {
  const id = nextId()
  $chatMessages.set([
    ...$chatMessages.get(),
    { id, role: 'user', text, attachments: attachments?.length ? attachments : undefined }
  ])

  return id
}

export interface PendingPromptItem {
  text: string
  attachments?: string[]
}

export const $pendingPromptBatch = atom<PendingPromptItem[]>([])

export function pushPendingPrompt(item: PendingPromptItem): void {
  $pendingPromptBatch.set([...$pendingPromptBatch.get(), item])
}

export function drainPendingPrompts(): PendingPromptItem[] {
  const items = $pendingPromptBatch.get()
  $pendingPromptBatch.set([])

  return items
}

export function clearPendingPrompts(): void {
  $pendingPromptBatch.set([])
}

export const $chatTurnInFlight = atom<boolean>(false)

// Set when the backend emitted a bubble.break during the in-flight turn — used
// so message.complete finalizes the LAST bubble with its own streamed text
// instead of overwriting it with the full multi-bubble turn text.
export const $turnHadBubbleBreak = atom<boolean>(false)

export function setTurnHadBubbleBreak(v: boolean): void {
  $turnHadBubbleBreak.set(v)
}

// Coalescing window for rapid-fire user messages (DESIGN §6.6 scenario 3):
// messages sent within this window are batched into ONE prompt.submit → one
// LLM call. This is an intentional debounce, NOT a send delay — schedulePendingFlush
// is reset on every keystroke-send, and the batch is drained immediately on
// message.complete / error / user stop via submitPendingBatch / handleStop.
const FLUSH_DEBOUNCE_MS = 4000
let flushTimer: ReturnType<typeof setTimeout> | null = null

export function schedulePendingFlush(): void {
  if (flushTimer) {
    clearTimeout(flushTimer)
  }

  flushTimer = setTimeout(() => {
    flushTimer = null
    submitPendingBatch()
  }, FLUSH_DEBOUNCE_MS)
}

export function cancelPendingFlush(): void {
  if (flushTimer) {
    clearTimeout(flushTimer)
    flushTimer = null
  }
}

/** Drain the pending prompt batch and submit it as one coalesced turn. */
export function submitPendingBatch(): void {
  if ($chatTurnInFlight.get()) {
    return
  }

  const sessionId = $chatSessionId.get()
  const gateway = $gateway.get()

  if (!sessionId || !gateway || gateway.connectionState !== 'open') {
    return
  }

  const pendingBatch = drainPendingPrompts()

  if (pendingBatch.length === 0) {
    return
  }

  $chatTurnInFlight.set(true)

  const batchPayload = {
    session_id: sessionId,
    batch: pendingBatch.map(p => ({
      text: p.text,
      ...(p.attachments?.length ? { attachments: p.attachments.map(file_url => ({ file_url, type: 'image' })) } : {})
    }))
  }

  const submitWithRetry = async (attempt = 0): Promise<void> => {
    const g = $gateway.get()

    if (!g || g.connectionState !== 'open') {
      $chatTurnInFlight.set(false)

      return
    }

    try {
      setSpriteState('thinking')
      await g.request('prompt.submit', batchPayload)
    } catch (err: unknown) {
      const errMsg = err instanceof Error ? err.message : String(err)

      if (errMsg.includes('in-flight') && attempt < 3) {
        await sleep(50 * Math.pow(2, attempt))

        return submitWithRetry(attempt + 1)
      }

      setAssistantError(err instanceof Error ? err.message : '发送失败')
      setSpriteState('idle')
      $chatTurnInFlight.set(false)
    }
  }

  void submitWithRetry()
}

// Ensure an active streaming assistant bubble exists before populating delta or tool state.
export function beginAssistantMessage(): void {
  const msgs = $chatMessages.get()
  const last = msgs[msgs.length - 1]

  if (last?.role === 'assistant' && last.streaming) {
    if (!last.text.trim() && !last.toolName && !last.error && !last.cancelled) {
      // Prior streaming bubble has no visible text or tools; reuse it to prevent ghost "…" bubbles
      return
    }

    const finalized: ChatMessage = { ...last, streaming: false }
    const id = nextId()
    $chatMessages.set([...msgs.slice(0, -1), finalized, { id, role: 'assistant', text: '', streaming: true }])

    return
  }

  const id = nextId()
  $chatMessages.set([...msgs, { id, role: 'assistant', text: '', streaming: true }])
}

export function ensureAssistantMessage(): void {
  const msgs = $chatMessages.get()
  const last = msgs[msgs.length - 1]

  if (last && last.role === 'assistant' && last.streaming) {
    return
  }

  beginAssistantMessage()
}

export function appendAssistantDelta(text: string): void {
  ensureAssistantMessage()
  const msgs = $chatMessages.get()
  const last = msgs[msgs.length - 1]

  if (!last || last.role !== 'assistant') {
    return
  }

  $chatMessages.set([...msgs.slice(0, -1), { ...last, text: last.text + text }])
}

export function setAssistantTool(name: string | null): void {
  ensureAssistantMessage()
  const msgs = $chatMessages.get()
  const last = msgs[msgs.length - 1]

  if (!last || last.role !== 'assistant') {
    return
  }

  $chatMessages.set([...msgs.slice(0, -1), { ...last, toolName: name }])
}

export function finalizeAssistantMessage(text?: string): void {
  const msgs = $chatMessages.get()
  const last = msgs[msgs.length - 1]

  if (!last || last.role !== 'assistant') {
    return
  }

  const finalStr = typeof text === 'string' ? text : last.text

  // If the assistant message is empty and has no tool/error/cancellation, prune it to prevent ghost bubble
  if (!finalStr.trim() && !last.toolName && !last.error && !last.cancelled && !last.attachments?.length) {
    $chatMessages.set(msgs.slice(0, -1))

    return
  }

  const finalized: ChatMessage = { ...last, text: finalStr, streaming: false, toolName: null }
  $chatMessages.set([...msgs.slice(0, -1), finalized])
}

export function setAssistantError(message: string): void {
  const msgs = $chatMessages.get()
  const last = msgs[msgs.length - 1]
  const isStreaming = last?.role === 'assistant' && last.streaming

  const error: ChatMessage = isStreaming
    ? { ...last, streaming: false, error: message }
    : { id: nextId(), role: 'assistant', text: '', error: message }

  // Replace when finalizing a streaming assistant message; otherwise append
  // so we don't clobber the user's last message.
  $chatMessages.set(isStreaming ? [...msgs.slice(0, -1), error] : [...msgs, error])
}

// User-initiated stop before the first assistant chunk arrived. Distinct from
// setAssistantError because cancellation isn't a failure — the bubble should
// render neutrally, not with the 😬 error glyph.
export function setAssistantCancelled(): void {
  const msgs = $chatMessages.get()
  const last = msgs[msgs.length - 1]
  const isStreaming = last?.role === 'assistant' && last.streaming

  const cancelled: ChatMessage = isStreaming
    ? { ...last, streaming: false, cancelled: true }
    : { id: nextId(), role: 'assistant', text: '', cancelled: true }

  // Replace when finalizing a streaming assistant message; otherwise append
  // so we don't clobber the user's last message.
  $chatMessages.set(isStreaming ? [...msgs.slice(0, -1), cancelled] : [...msgs, cancelled])
}

export function clearChat(): void {
  clearPendingPrompts()
  $chatMessages.set([])
  $chatSessionId.set(null)
}
