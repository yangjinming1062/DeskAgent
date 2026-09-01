import { atom, map } from 'nanostores'

import { setSpriteState } from '@/companion/companion-store'
import {
  persistString,
  registerCompanionStorageKey,
  registerStorageClearHandler,
  storedString
} from '@/shared/lib/storage'
import { sleep } from '@/shared/lib/utils'
import { $gateway } from '@/shared/store/gateway'
import type { ChatAttachment, ChatMediaItem, SessionMessage, SessionRuntimeInfo } from '@/shared/types/spiritagent'

// 消息身份与稳定元数据，挂载于 $chatMessageList。
export interface ChatMessageListItem {
  id: string
  role: 'user' | 'assistant'
  subtype?: string
  /** 后端 Message.id——fork 按钮回传给后端的 source_message_id；仅 hydrate 的历史消息有值，活路径 push 出的消息为 undefined。 */
  backendMessageId?: number
}

// 消息可变主体，按 id 键入 $chatMessageBodies。
export interface ChatMessageBody {
  text: string
  streaming?: boolean
  toolName?: string | null
  error?: string
  cancelled?: boolean
  attachments?: ChatAttachment[]
  media?: ChatMediaItem[]
}

const DEFAULT_CONTEXT_LIMIT = 1_000_000
const CHAT_SESSION_ID_KEY = registerCompanionStorageKey('da.companion.chatSessionId')
const FLUSH_DEBOUNCE_MS = 4000

let idCounter = 0
const nextId = (): string => `m${++idCounter}`

let mediaHintTimer: ReturnType<typeof setTimeout> | null = null
let flushTimer: ReturnType<typeof setTimeout> | null = null

export const $chatMessageList = atom<ChatMessageListItem[]>([])
export const $chatMessageBodies = map<Record<string, ChatMessageBody>>({})
// 尾部助手消息是否处于流式中。ChatDock 借此感知生成状态，无需全量订阅 bodies。
export const $lastAssistantStreaming = atom<boolean>(false)
// 流式增量递增计数器，供 ChatScrollAutoFollow 独立订阅触发滚动，不重渲染 ChatDock 容器。
export const $chatStreamingTick = atom<number>(0)
export const $chatSessionId = atom<string | null>(storedString(CHAT_SESSION_ID_KEY))
export const $chatOpen = atom(false)
// IM 守卫与语音入口的权威 kind 源：写值由 hydrate 把服务端 info.kind 注入。
export const $chatSessionKind = atom<string>('standard')

// 当前会话独立参数配置（温度、压缩阈值、思考程度等）
export interface SessionSettings {
  temperature?: number
  context_compression_threshold?: number
  enable_context_compression?: boolean
  reasoning_effort?: string
}

export const $sessionSettings = atom<SessionSettings>({})

export function setSessionSettings(settings: SessionSettings): void {
  $sessionSettings.set(settings)
}

export function updateSessionSetting<K extends keyof SessionSettings>(key: K, value: SessionSettings[K]): void {
  $sessionSettings.set({
    ...$sessionSettings.get(),
    [key]: value
  })
}

// 上下文使用量状态跟踪（已用 token、总容量、压缩阈值节点等）
export interface SessionContextUsage {
  promptTokens: number
  completionTokens: number
  totalTokens: number
  contextLimit: number
}

export const $sessionContextUsage = atom<SessionContextUsage>({
  promptTokens: 0,
  completionTokens: 0,
  totalTokens: 0,
  contextLimit: DEFAULT_CONTEXT_LIMIT
})

export function setSessionContextUsage(usage: Partial<SessionContextUsage>): void {
  const current = $sessionContextUsage.get()
  const promptTokens = usage.promptTokens ?? current.promptTokens
  const completionTokens = usage.completionTokens ?? current.completionTokens

  const totalTokens =
    usage.totalTokens ??
    (usage.promptTokens !== undefined || usage.completionTokens !== undefined
      ? promptTokens + completionTokens
      : current.totalTokens)

  const contextLimit = usage.contextLimit ?? current.contextLimit

  $sessionContextUsage.set({
    promptTokens,
    completionTokens,
    totalTokens,
    contextLimit
  })
}

export function resetSessionContextUsage(contextLimit?: number): void {
  $sessionContextUsage.set({
    promptTokens: 0,
    completionTokens: 0,
    totalTokens: 0,
    contextLimit: contextLimit ?? DEFAULT_CONTEXT_LIMIT
  })
}

// 待发送附件：支持图片、视频、普通文件、文件夹 4 种类型
export type PendingAttachment =
  | { type: 'image'; value: string; fileName?: string }
  | {
      type: 'video'
      fileName: string
      path: string
      status: 'error' | 'ready' | 'uploading'
      url?: string
      error?: string
    }
  | {
      type: 'file'
      fileName: string
      path: string
    }
  | {
      type: 'folder'
      folderName: string
      path: string
    }

// 伙伴主动说出的瞬时消息，在聊天面板收起时以气泡形式浮出。说完后清空。
// sessionId 存在时点击气泡会切到该会话（媒体送达提示跳转用）。
export interface ProactiveBubbleState {
  text: string
  sessionId?: string
}

export const $proactiveBubble = atom<ProactiveBubbleState | null>(null)

// 外部投喂（DESIGN §6.3「文件投喂」）——SpriteStage 拖拽文件到精灵本体时，
// 把文件路径推到此处。ChatDock 订阅并把首个图像文件塞入附件占位。
interface PendingExternalAttachment {
  paths: string[]
  nonce: number
}

export const $pendingExternalAttachment = atom<PendingExternalAttachment | null>(null)

export function pushExternalAttachment(paths: string[]): void {
  $pendingExternalAttachment.set({ paths, nonce: Date.now() })
}

export function clearExternalAttachment(): void {
  $pendingExternalAttachment.set(null)
}

export function setChatOpen(open: boolean): void {
  $chatOpen.set(open)
}

export function setChatSession(id: string | null): void {
  clearPendingPrompts()
  cancelPendingFlush()
  $chatTurnInFlight.set(false)
  $turnHadBubbleBreak.set(false)
  $chatSessionId.set(id)
  persistString(CHAT_SESSION_ID_KEY, id)
  // setChatSession 是无 info 的重置路径；后续 hydrate 会以服务端权威 kind 覆盖此值。
  $chatSessionKind.set('standard')
}

// 用从后端加载的会话替换面板的聊天记录。
export function hydrateChatMessages(messages: SessionMessage[], info?: SessionRuntimeInfo): void {
  const items: ChatMessageListItem[] = []
  const bodies: Record<string, ChatMessageBody> = {}

  let totalChars = 0

  for (const m of messages) {
    const id = nextId()
    const textContent = extractText(m)
    totalChars += textContent.length

    items.push({
      id,
      role: m.role === 'user' ? 'user' : 'assistant',
      subtype: m.subtype,
      backendMessageId: typeof m.id === 'number' ? m.id : undefined
    })
    bodies[id] = {
      text: textContent,
      toolName: m.tool_name ?? null,
      streaming: false,
      ...(m.role === 'user' ? omitUndefined(extractUserAttachments(m)) : {}),
      ...(m.media?.length ? { media: m.media } : {})
    }
  }

  $chatMessageBodies.set(bodies)
  $chatMessageList.set(items)
  $lastAssistantStreaming.set(false)

  if (info?.settings) {
    $sessionSettings.set(info.settings as SessionSettings)
  }

  // 缺字段回落 standard，与 setChatSession 兜底一致——避免 IM 守卫在 hydrate 完成前的瞬间误判。
  $chatSessionKind.set(info?.kind ?? 'standard')

  // 估算 Token 占用（无精确 usage 时的兜底估算：~3 字符/Token）
  const approxTokens = Math.round(totalChars / 3)
  setSessionContextUsage({
    totalTokens: approxTokens,
    contextLimit: info?.context_window || DEFAULT_CONTEXT_LIMIT
  })
}

function extractText(m: SessionMessage): string {
  // ``SessionMessage.content`` 类型未知——部分用户消息以 JSON parts 数组
  // 到达（含 image_url 等多模态部分）；只渲染用户可见文本，避免漏出
  // ``[{"type": "input_image", ...}]`` 之类的非文本部分。
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

// 多模态用户行里的 input_image/input_video parts 还原为类型化附件列表，
// 供气泡渲染媒体卡；纯文本行与无附件行返回 undefined。被清理的视频行只剩
// [视频已清理] 文本 part，天然落不进附件列表。
function extractUserAttachments(m: SessionMessage): ChatAttachment[] | undefined {
  if (typeof m.content !== 'string') {
    return undefined
  }

  let parsed: unknown

  try {
    parsed = JSON.parse(m.content)
  } catch {
    return undefined
  }

  if (!Array.isArray(parsed)) {
    return undefined
  }

  const attachments = parsed
    .filter(
      (p): p is { type?: string; image_url?: unknown; video_url?: unknown } =>
        typeof p === 'object' &&
        p !== null &&
        ((p as { type?: unknown }).type === 'input_image' || (p as { type?: unknown }).type === 'input_video')
    )
    .map(p =>
      p.type === 'input_video'
        ? { type: 'video' as const, url: typeof p.video_url === 'string' ? p.video_url : '' }
        : { type: 'image' as const, url: typeof p.image_url === 'string' ? p.image_url : '' }
    )
    .filter(a => a.url.length > 0)

  return attachments.length ? attachments : undefined
}

function omitUndefined(attachments: ChatAttachment[] | undefined): { attachments?: ChatAttachment[] } {
  return attachments ? { attachments } : {}
}

export function setProactiveBubble(state: ProactiveBubbleState | null): void {
  $proactiveBubble.set(state)
}

export function showMediaHint(text: string, sessionId?: string): void {
  if (mediaHintTimer) {
    clearTimeout(mediaHintTimer)
  }

  $proactiveBubble.set(sessionId ? { text, sessionId } : { text })
  mediaHintTimer = setTimeout(() => {
    $proactiveBubble.set(null)
    mediaHintTimer = null
  }, 8000)
}

export function pushProactiveMessage(text: string): void {
  const id = nextId()
  $chatMessageBodies.setKey(id, { text, streaming: false, toolName: null })
  $chatMessageList.set([...$chatMessageList.get(), { id, role: 'assistant', subtype: 'status_proactive' }])
}

// 后台视频完成等异步送达的媒体行；与历史水合的 status_media 行同形状。
export function pushMediaMessage(media: ChatMediaItem[]): string {
  const id = nextId()
  $chatMessageBodies.setKey(id, { text: '', media, streaming: false, toolName: null })
  $chatMessageList.set([...$chatMessageList.get(), { id, role: 'assistant', subtype: 'status_media' }])

  return id
}

// DESIGN §6.6 场景 1：LLM 只声明情绪/动作、不输出正文的回合，在对话里留下
// 一条「情绪痕迹」弱化提示行（渲染端按 subtype=status_affect 走 trace 样式）。
// 后端对同一行为持久化 status_affect 行（chat/persistence.py），live 路径补齐
// 同一行，保证实时视图与历史水合一致——否则当场无声无息、重开聊天却多出一行。
export function pushAffectTraceMessage(): void {
  const id = nextId()
  $chatMessageBodies.setKey(id, { text: '', streaming: false, toolName: null })
  $chatMessageList.set([...$chatMessageList.get(), { id, role: 'assistant', subtype: 'status_affect' }])
}

export function pushUserMessage(text: string, attachments?: ChatAttachment[]): string {
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

/**
 * 把一行 status pill（如 `status_cleared` / `compress_summary` / 自定义 command_result）插入消息列表。
 *
 * Pill 在渲染层走 `status_*` / `compress_summary` 通用路径（与每日摘要、压缩摘要同形态）。
 * 文本为空时返回的 id 是新插入消息的本地 id（便于滚动定位等场景）。
 */
export function pushStatusPill(subtype: string, text: string): string {
  const id = nextId()
  $chatMessageBodies.setKey(id, {
    text,
    streaming: false,
    toolName: null
  })
  $chatMessageList.set([...$chatMessageList.get(), { id, role: 'assistant', subtype }])

  return id
}

export interface PendingPromptItem {
  text: string
  attachments?: ChatAttachment[]
}

export const $pendingPromptBatch = atom<PendingPromptItem[]>([])

export function pushPendingPrompt(item: PendingPromptItem): void {
  $pendingPromptBatch.set([...$pendingPromptBatch.get(), item])
}

function drainPendingPrompts(): PendingPromptItem[] {
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

export interface ChatUndoDraft {
  session_id: string
  text: string
  content_type?: string
  media_json?: string | null
}

// 撤回落草稿总线：undo 成功后由 session-list-store 写入；多窗口订阅需按 session_id 过滤，避免 A 撤回落到 B 的输入框。
export const $chatDraftFromUndo = atom<ChatUndoDraft | null>(null)

registerStorageClearHandler(() => {
  $chatSessionId.set(null)
  $chatMessageList.set([])
  $chatMessageBodies.set({})
  $lastAssistantStreaming.set(false)
  $chatStreamingTick.set(0)
  $chatOpen.set(false)
  $chatSessionKind.set('standard')
  $sessionSettings.set({})
  $sessionContextUsage.set({
    completionTokens: 0,
    contextLimit: DEFAULT_CONTEXT_LIMIT,
    promptTokens: 0,
    totalTokens: 0
  })
  $proactiveBubble.set(null)
  $pendingExternalAttachment.set(null)
  $pendingPromptBatch.set([])
  $chatTurnInFlight.set(false)
  $turnHadBubbleBreak.set(false)
  $chatDraftFromUndo.set(null)

  if (flushTimer) {
    clearTimeout(flushTimer)
    flushTimer = null
  }

  if (mediaHintTimer) {
    clearTimeout(mediaHintTimer)
    mediaHintTimer = null
  }
})

export function setTurnHadBubbleBreak(v: boolean): void {
  $turnHadBubbleBreak.set(v)
}

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
      ...(p.attachments?.length ? { attachments: p.attachments.map(a => ({ file_url: a.url, type: a.type })) } : {})
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

      // thinking（50）> idle（10）：不带 force 会被优先级门控吞掉，精灵卡在思考态。
      setAssistantError(err instanceof Error ? err.message : '发送失败')
      setSpriteState('idle', { force: true })
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

function ensureAssistantMessage(): void {
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

export function finalizeAssistantMessage(text?: string, media?: ChatMediaItem[]): void {
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
  const finalMedia = media ?? body.media

  // 助手消息为空且无工具/错误/取消/媒体时剪掉，避免空白气泡。
  const isEmpty =
    !finalStr.trim() &&
    !body.toolName &&
    !body.error &&
    !body.cancelled &&
    !body.attachments?.length &&
    !finalMedia?.length

  if (isEmpty) {
    $chatMessageList.set(list.slice(0, -1))
    $chatMessageBodies.setKey(lastItem.id, undefined)
    $lastAssistantStreaming.set(false)

    return
  }

  $chatMessageBodies.setKey(lastItem.id, {
    ...body,
    text: finalStr,
    media: finalMedia,
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

// 重置消息列表与 bodies，不触碰 $chatSessionId 与 pending batch。
export function resetChatMessages(): void {
  $chatMessageList.set([])
  $chatMessageBodies.set({})
  $lastAssistantStreaming.set(false)
  $chatTurnInFlight.set(false)
  $turnHadBubbleBreak.set(false)
}
