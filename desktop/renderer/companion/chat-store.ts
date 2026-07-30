import { atom } from 'nanostores'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  streaming?: boolean
  attachments?: string[]
  toolName?: string | null
  error?: string
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
  $chatSessionId.set(id)
}

export function setProactiveBubble(text: string | null): void {
  $proactiveBubble.set(text)
}

export function pushUserMessage(text: string, attachments?: string[]): string {
  const id = nextId()
  $chatMessages.set([
    ...$chatMessages.get(),
    { id, role: 'user', text, attachments: attachments?.length ? attachments : undefined }
  ])

  return id
}

// Begin a streaming assistant segment. Called on `message.start`. If the last
// message is already a streaming assistant one (e.g. back-to-back starts after
// a tool round), finalize it in place first so segments don't merge.
export function beginAssistantMessage(): void {
  const msgs = $chatMessages.get()
  const last = msgs[msgs.length - 1]
  const rest = last?.streaming ? [...msgs.slice(0, -1), { ...last, streaming: false }] : msgs
  const id = nextId()
  $chatMessages.set([...rest, { id, role: 'assistant', text: '', streaming: true }])
}

export function appendAssistantDelta(text: string): void {
  const msgs = $chatMessages.get()
  const last = msgs[msgs.length - 1]

  if (!last || last.role !== 'assistant') {return}
  $chatMessages.set([...msgs.slice(0, -1), { ...last, text: last.text + text }])
}

export function setAssistantTool(name: string | null): void {
  const msgs = $chatMessages.get()
  const last = msgs[msgs.length - 1]

  if (!last || last.role !== 'assistant') {return}
  $chatMessages.set([...msgs.slice(0, -1), { ...last, toolName: name }])
}

export function finalizeAssistantMessage(text?: string): void {
  const msgs = $chatMessages.get()
  const last = msgs[msgs.length - 1]

  if (!last) {return}
  const finalized: ChatMessage = { ...last, streaming: false, toolName: null }

  if (typeof text === 'string') {finalized.text = text}
  $chatMessages.set([...msgs.slice(0, -1), finalized])
}

export function setAssistantError(message: string): void {
  const msgs = $chatMessages.get()
  const last = msgs[msgs.length - 1]

  const error: ChatMessage =
    last?.role === 'assistant' && last.streaming
      ? { ...last, streaming: false, error: message }
      : { id: nextId(), role: 'assistant', text: '', error: message }

  $chatMessages.set(last ? [...msgs.slice(0, -1), error] : [error])
}

export function clearChat(): void {
  $chatMessages.set([])
  $chatSessionId.set(null)
}
