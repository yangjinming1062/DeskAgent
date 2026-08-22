import { atom, map } from 'nanostores'

import { setSpriteState } from '@/companion/companion-store'
import { sleep } from '@/shared/lib/utils'
import { $gateway } from '@/shared/store/gateway'
import type { SessionMessage } from '@/shared/types/spiritagent'

// 消息身份与稳定元数据，挂载于 $chatMessageList。
export interface ChatMessageListItem {
  id: string
  role: 'user' | 'assistant'
  subtype?: string
}

// 消息可变主体，按 id 键入 $chatMessageBodies。
export interface ChatMessageBody {
  text: string
  streaming?: boolean
  toolName?: string | null
  error?: string
  cancelled?: boolean
  attachments?: string[]
}

export interface ChatMessage extends ChatMessageListItem, ChatMessageBody {}

export const $chatMessageList = atom<ChatMessageListItem[]>([])
export const $chatMessageBodies = map<Record<string, ChatMessageBody>>({})
// 尾部助手消息是否处于流式中。ChatDock 借此感知生成状态，无需全量订阅 bodies。
export const $lastAssistantStreaming = atom<boolean>(false)
// 流式增量递增计数器，供 ChatScrollAutoFollow 独立订阅触发滚动，不重渲染 ChatDock 容器。
export const $chatStreamingTick = atom<number>(0)
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
  cancelPendingFlush()
  $chatTurnInFlight.set(false)
  $turnHadBubbleBreak.set(false)
  $chatSessionId.set(id)
}

// 用从后端加载的会话替换面板的聊天记录。
export function hydrateChatMessages(messages: SessionMessage[]): void {
  const items: ChatMessageListItem[] = []
  const bodies: Record<string, ChatMessageBody> = {}

  for (const m of messages) {
    const id = nextId()
    items.push({
      id,
      role: m.role === 'user' ? 'user' : 'assistant',
      subtype: m.subtype
    })
    bodies[id] = {
      text: extractText(m),
      toolName: m.tool_name ?? null,
      streaming: false
    }
  }

  $chatMessageBodies.set(bodies)
  $chatMessageList.set(items)
  $lastAssistantStreaming.set(false)
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
  const id = nextId()
  $chatMessageBodies.setKey(id, { text, streaming: false, toolName: null })
  $chatMessageList.set([...$chatMessageList.get(), { id, role: 'assistant', subtype: 'status_proactive' }])
}

export function pushUserMessage(text: string, attachments?: string[]): string {
  const id = nextId()
  $chatMessageBodies.setKey(id, {
    text,
    attachments: attachments?.length ? attachments : undefined,
    streaming: false,
    toolName: null
  })
  $chatMessageList.set([...$chatMessageList.get(), { id, role: 'user' }])

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

// 当后端在 in-flight 回合期间发出 bubble.break 时置位，防止 message.complete 全文覆盖末尾气泡。
export const $turnHadBubbleBreak = atom<boolean>(false)

export function setTurnHadBubbleBreak(v: boolean): void {
  $turnHadBubbleBreak.set(v)
}

// 连发消息的合并窗口（在窗口内合并为一次 prompt.submit）。
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

// 冲刷排队的提示批次并作为合并回合提交。
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

// 确保存在活跃的流式助手气泡。
export function beginAssistantMessage(): void {
  const list = $chatMessageList.get()
  const lastItem = list[list.length - 1]
  const lastBody = lastItem ? $chatMessageBodies.get()[lastItem.id] : undefined

  // 复用无内容的流式气泡，避免出现空白占位。
  if (lastItem?.role === 'assistant' && lastBody?.streaming) {
    if (!lastBody.text.trim() && !lastBody.toolName && !lastBody.error && !lastBody.cancelled) {
      return
    }

    // 多气泡 break：结束上一段并开启新段。
    const finalizedBody: ChatMessageBody = {
      ...lastBody,
      streaming: false,
      toolName: null
    }

    const newId = nextId()
    $chatMessageBodies.setKey(lastItem.id, finalizedBody)
    $chatMessageBodies.setKey(newId, { text: '', streaming: true, toolName: null })
    $chatMessageList.set([...list, { id: newId, role: 'assistant' }])
    $lastAssistantStreaming.set(true)

    return
  }

  const id = nextId()
  $chatMessageBodies.setKey(id, { text: '', streaming: true, toolName: null })
  $chatMessageList.set([...list, { id, role: 'assistant' }])
  $lastAssistantStreaming.set(true)
}

export function ensureAssistantMessage(): void {
  const list = $chatMessageList.get()
  const lastItem = list[list.length - 1]
  const lastBody = lastItem ? $chatMessageBodies.get()[lastItem.id] : undefined

  if (lastItem && lastItem.role === 'assistant' && lastBody?.streaming) {
    return
  }

  beginAssistantMessage()
}

export function appendAssistantDelta(text: string): void {
  ensureAssistantMessage()
  const list = $chatMessageList.get()
  const lastItem = list[list.length - 1]

  if (!lastItem || lastItem.role !== 'assistant') {
    return
  }

  const body = $chatMessageBodies.get()[lastItem.id]

  if (!body) {
    return
  }

  // 仅更新当前流式消息 body，不改动 list 引用。
  $chatMessageBodies.setKey(lastItem.id, { ...body, text: body.text + text })
  $chatStreamingTick.set($chatStreamingTick.get() + 1)
}

export function setAssistantTool(name: string | null): void {
  ensureAssistantMessage()
  const list = $chatMessageList.get()
  const lastItem = list[list.length - 1]

  if (!lastItem || lastItem.role !== 'assistant') {
    return
  }

  const body = $chatMessageBodies.get()[lastItem.id]

  if (!body) {
    return
  }

  $chatMessageBodies.setKey(lastItem.id, { ...body, toolName: name })
}

export function finalizeAssistantMessage(text?: string): void {
  const list = $chatMessageList.get()
  const lastItem = list[list.length - 1]

  if (!lastItem || lastItem.role !== 'assistant') {
    return
  }

  const body = $chatMessageBodies.get()[lastItem.id]

  if (!body) {
    return
  }

  const finalStr = typeof text === 'string' ? text : body.text

  // 助手消息为空且无工具/错误/取消时剪掉，避免空白气泡。
  const isEmpty = !finalStr.trim() && !body.toolName && !body.error && !body.cancelled && !body.attachments?.length

  if (isEmpty) {
    $chatMessageList.set(list.slice(0, -1))
    $chatMessageBodies.setKey(lastItem.id, undefined)
    $lastAssistantStreaming.set(false)

    return
  }

  $chatMessageBodies.setKey(lastItem.id, {
    ...body,
    text: finalStr,
    streaming: false,
    toolName: null
  })
  $lastAssistantStreaming.set(false)
}

export function setAssistantError(message: string): void {
  const list = $chatMessageList.get()
  const lastItem = list[list.length - 1]
  const lastBody = lastItem ? $chatMessageBodies.get()[lastItem.id] : undefined
  const isStreaming = lastItem?.role === 'assistant' && lastBody?.streaming

  if (isStreaming && lastItem && lastBody) {
    $chatMessageBodies.setKey(lastItem.id, {
      ...lastBody,
      streaming: false,
      error: message
    })
    $lastAssistantStreaming.set(false)

    return
  }

  const id = nextId()
  $chatMessageBodies.setKey(id, {
    text: '',
    error: message,
    streaming: false,
    toolName: null
  })
  $chatMessageList.set([...list, { id, role: 'assistant' }])
  $lastAssistantStreaming.set(false)
}

// 用户在第一条助手片段到达前主动停止。
export function setAssistantCancelled(): void {
  const list = $chatMessageList.get()
  const lastItem = list[list.length - 1]
  const lastBody = lastItem ? $chatMessageBodies.get()[lastItem.id] : undefined
  const isStreaming = lastItem?.role === 'assistant' && lastBody?.streaming

  if (isStreaming && lastItem && lastBody) {
    $chatMessageBodies.setKey(lastItem.id, {
      ...lastBody,
      streaming: false,
      cancelled: true
    })
    $lastAssistantStreaming.set(false)

    return
  }

  const id = nextId()
  $chatMessageBodies.setKey(id, {
    text: '',
    cancelled: true,
    streaming: false,
    toolName: null
  })
  $chatMessageList.set([...list, { id, role: 'assistant' }])
  $lastAssistantStreaming.set(false)
}

export function clearChat(): void {
  clearPendingPrompts()
  cancelPendingFlush()
  $chatMessageList.set([])
  $chatMessageBodies.set({})
  $chatSessionId.set(null)
  $lastAssistantStreaming.set(false)
  $chatStreamingTick.set(0)
  $chatTurnInFlight.set(false)
  $turnHadBubbleBreak.set(false)
}

// 重置消息列表与 bodies，不触碰 $chatSessionId 与 pending batch。
export function resetChatMessages(): void {
  $chatMessageList.set([])
  $chatMessageBodies.set({})
  $lastAssistantStreaming.set(false)
  $chatTurnInFlight.set(false)
  $turnHadBubbleBreak.set(false)
}
