import type { RpcEvent } from '../gateway/types'
import {
  beginAssistantMessage,
  appendDelta,
  finalizeAssistantMessage
} from './chat-store'
import { setSpriteState, type SpriteEmotion } from './companion-store'
import { speak } from '../tts'

function asEmotion(v: unknown): SpriteEmotion | null {
  return typeof v === 'string' && v !== 'neutral' ? (v as SpriteEmotion) : null
}

let speakingTimer: ReturnType<typeof setTimeout> | null = null

/** Shared speak-then-idle lifecycle for both chat replies and proactive messages. */
function playTts(text: string, emotion: SpriteEmotion | null): void {
  if (speakingTimer) { clearTimeout(speakingTimer); speakingTimer = null }
  const startSpeaking = () => {
    speakingTimer = null
    setSpriteState('speaking', { force: true })
    if (text) {
      speak(text).then(() => setSpriteState('idle', { force: true }))
    } else {
      setSpriteState('idle', { force: true })
    }
  }
  if (emotion) {
    setSpriteState('emotional', { emotion })
    speakingTimer = setTimeout(startSpeaking, 1200)
  } else {
    startSpeaking()
  }
}

export function createEventHandler(deps: {
  onModelReady: (url: string) => void
  onWardrobeUpdate: () => void
}): (event: RpcEvent) => void {
  return (event: RpcEvent) => {
    const p = (event.payload ?? {}) as Record<string, unknown>

    switch (event.type) {
      case 'message.start':
        beginAssistantMessage()
        setSpriteState('thinking')
        break

      case 'message.delta':
        if (typeof p.text === 'string') appendDelta(p.text)
        break

      case 'message.complete': {
        const text = typeof p.text === 'string' ? p.text : ''
        finalizeAssistantMessage(text)
        const affect = p.affect as { emotion?: string } | undefined
        playTts(text, asEmotion(affect?.emotion))
        break
      }

      case 'companion.affect': {
        const emotion = asEmotion(p.emotion)
        if (emotion) setSpriteState('emotional', { emotion })
        break
      }

      case 'companion.message': {
        const text = typeof p.text === 'string' ? p.text : ''
        const affect = p.affect as { emotion?: string } | undefined
        playTts(text, asEmotion(affect?.emotion))
        break
      }

      case 'tool.start':
        setSpriteState('working')
        break

      case 'tool.complete':
        setSpriteState('thinking', { force: true })
        break

      case 'tool.call':
        // Standalone client has no runner — tool calls are not serviced.
        // The LLM turn will time out on the backend side. Use a tool-free
        // LLM config for this demo client.
        break

      case 'model.ready': {
        const url = typeof p.asset_url === 'string' ? p.asset_url : null
        if (url) deps.onModelReady(url)
        break
      }

      case 'wardrobe.updated':
        deps.onWardrobeUpdate()
        break

      case 'error':
        finalizeAssistantMessage()
        setSpriteState('idle', { force: true })
        break
    }
  }
}
