import { atom, map } from 'nanostores'

import type { ChatAttachment, ChatMediaItem } from '@/shared/types/spiritagent'

export interface VoiceMessageListItem {
  id: string
  role: 'user' | 'assistant'
  subtype?: string
}

export interface VoiceMessageBody {
  text: string
  streaming?: boolean
  toolName?: string | null
  error?: string
  cancelled?: boolean
  attachments?: ChatAttachment[]
  media?: ChatMediaItem[]
}

export const $voiceMessageList = atom<VoiceMessageListItem[]>([])
export const $voiceMessageBodies = map<Record<string, VoiceMessageBody>>({})
export const $voiceLastAssistantStreaming = atom<boolean>(false)
export const $voiceStreamingTick = atom<number>(0)

let idCounter = 0
const nextId = () => `v${++idCounter}`

export function resetVoiceMessages(): void {
  $voiceMessageList.set([])
  $voiceMessageBodies.set({})
  $voiceLastAssistantStreaming.set(false)
}

export function pushVoiceUserMessage(text: string): void {
  if (!text) {
    return
  }

  const id = nextId()
  $voiceMessageList.set([...$voiceMessageList.get(), { id, role: 'user' }])
  $voiceMessageBodies.setKey(id, { text })
  $voiceStreamingTick.set($voiceStreamingTick.get() + 1)
}

export function beginVoiceAssistantMessage(): string {
  const id = nextId()
  $voiceMessageList.set([...$voiceMessageList.get(), { id, role: 'assistant' }])
  $voiceMessageBodies.setKey(id, { text: '', streaming: true })
  $voiceLastAssistantStreaming.set(true)
  $voiceStreamingTick.set($voiceStreamingTick.get() + 1)
  return id
}

export function appendVoiceAssistantDelta(text: string): void {
  if (!text) {
    return
  }
  const list = $voiceMessageList.get()
  const bodies = { ...$voiceMessageBodies.get() }
  for (let i = list.length - 1; i >= 0; i--) {
    if (list[i].role === 'assistant' && bodies[list[i].id]?.streaming) {
      bodies[list[i].id] = { ...bodies[list[i].id], text: bodies[list[i].id].text + text }
      break
    }
  }
  $voiceMessageBodies.set(bodies)
  $voiceStreamingTick.set($voiceStreamingTick.get() + 1)
}

export function finalizeVoiceAssistantMessage(text: string, media?: ChatMediaItem[]): void {
  const list = $voiceMessageList.get()
  const bodies = { ...$voiceMessageBodies.get() }
  for (let i = list.length - 1; i >= 0; i--) {
    if (list[i].role === 'assistant') {
      const prev = bodies[list[i].id] ?? {}
      bodies[list[i].id] = {
        text,
        streaming: false,
        ...(prev.toolName !== undefined ? { toolName: prev.toolName } : {}),
        ...(media?.length ? { media } : {})
      }
      break
    }
  }
  $voiceMessageBodies.set(bodies)
  $voiceLastAssistantStreaming.set(false)
}

export function setVoiceAssistantError(message: string): void {
  const list = $voiceMessageList.get()
  const bodies = { ...$voiceMessageBodies.get() }
  for (let i = list.length - 1; i >= 0; i--) {
    if (list[i].role === 'assistant') {
      bodies[list[i].id] = { ...(bodies[list[i].id] ?? { text: '' }), error: message, streaming: false }
      break
    }
  }
  $voiceMessageBodies.set(bodies)
  $voiceLastAssistantStreaming.set(false)
}
