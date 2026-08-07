import { atom } from 'nanostores'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  pending?: boolean
}

export const $messages = atom<ChatMessage[]>([])
export const $chatOpen = atom(false)
export const $sessionId = atom<string | null>(null)
export const $assistantMessageId = atom<string | null>(null)
// In-progress streaming text for the current assistant message. Kept as a
// separate atom so per-token updates are O(1) instead of O(n) array copy.
export const $streamingText = atom<string>('')

let counter = 0
const nextId = (): string => `m${++counter}`

export function addUserMessage(text: string): void {
  $messages.set([...$messages.get(), { id: nextId(), role: 'user', text }])
}

export function beginAssistantMessage(): string {
  const id = nextId()
  $assistantMessageId.set(id)
  $streamingText.set('')
  $messages.set([...$messages.get(), { id, role: 'assistant', text: '', pending: true }])
  return id
}

export function appendDelta(delta: string): void {
  // O(1) per token; ChatDock reads $streamingText for the in-progress
  // message and merges with $messages[].text on render.
  $streamingText.set($streamingText.get() + delta)
}

export function finalizeAssistantMessage(finalText?: string): void {
  const id = $assistantMessageId.get()
  $assistantMessageId.set(null)
  if (!id) return
  const msgs = $messages.get()
  const msg = msgs.find(m => m.id === id)
  if (msg) {
    msg.pending = false
    msg.text = finalText ?? $streamingText.get()
    $messages.set([...msgs])
  }
  $streamingText.set('')
}
