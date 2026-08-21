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
// 伙伴主动说出的瞬时消息，在聊天面板收起时以气泡形式浮出。说完后清空。
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

/** 用从后端加载的会话替换面板的聊天记录。
 * `system` / `tool` 行保留 subtype，以便气泡渲染器选择正确的呈现；布局上折叠到助手侧。 */
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
  // ``SessionMessage.content`` 类型未知——multimodal_v1 的用户消息
  // 以 JSON parts 数组到达；只渲染用户可见文本，避免漏出 ``[{"type": "input_image", ...}]``。
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
    .filter(p => p.type === 'input_text' && typeof p.text === 'string')
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

// 当后端在 in-flight 回合期间发出 bubble.break 时置位——这样 message.complete
// 收尾的是最后一个气泡自己的流式文本，而不是用整轮多气泡文本覆盖它。
export const $turnHadBubbleBreak = atom<boolean>(false)

export function setTurnHadBubbleBreak(v: boolean): void {
  $turnHadBubbleBreak.set(v)
}

// 连发消息的合并窗口（DESIGN §6.6 场景 3）：
// 在该窗口内连发的多条消息会被合并成一次 prompt.submit → 一次 LLM 调用。
// 这是**刻意**的合并，不是发送延迟——schedulePendingFlush 在每次按键发送时重置，
// 而批量会在 message.complete / 错误 / 用户停止时通过 submitPendingBatch / handleStop 立即冲刷。
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

// 在填充增量或工具状态前，确保存在一个活跃的流式助手气泡。
export function beginAssistantMessage(): void {
  const msgs = $chatMessages.get()
  const last = msgs[msgs.length - 1]

  if (last?.role === 'assistant' && last.streaming) {
    if (!last.text.trim() && !last.toolName && !last.error && !last.cancelled) {
      // 之前的流式气泡没有可见文本或工具调用；复用它以避免出现幽灵的「…」气泡
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

  // 助手消息为空且无工具/错误/取消时，剪掉以避免出现幽灵气泡
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

  // 收尾流式助手消息时替换；否则追加，避免覆盖用户的最后一条消息。
  $chatMessages.set(isStreaming ? [...msgs.slice(0, -1), error] : [...msgs, error])
}

// 用户在第一条助手片段到达前主动停止。与 setAssistantError 的区别在于：
// 取消不是失败——气泡应以中性样式渲染，而不是带 😬 错误图标。
export function setAssistantCancelled(): void {
  const msgs = $chatMessages.get()
  const last = msgs[msgs.length - 1]
  const isStreaming = last?.role === 'assistant' && last.streaming

  const cancelled: ChatMessage = isStreaming
    ? { ...last, streaming: false, cancelled: true }
    : { id: nextId(), role: 'assistant', text: '', cancelled: true }

  // 收尾流式助手消息时替换；否则追加，避免覆盖用户的最后一条消息。
  $chatMessages.set(isStreaming ? [...msgs.slice(0, -1), cancelled] : [...msgs, cancelled])
}

export function clearChat(): void {
  clearPendingPrompts()
  $chatMessages.set([])
  $chatSessionId.set(null)
}
