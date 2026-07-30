import {
  appendAssistantDelta,
  beginAssistantMessage,
  finalizeAssistantMessage,
  setAssistantError,
  setAssistantTool
} from '@/companion/chat-store'
import { setClipStatus } from '@/companion/clip-store'
import { $disturbanceTier, setSpriteState, type SpriteEmotion } from '@/companion/companion-store'
import type { RpcEvent } from '@/shared/types/deskagent'

import { speakProactive } from './proactive/proactive'

export function handleCompanionEvent(event: RpcEvent): void {
  switch (event.type) {
    case 'message.start':
      beginAssistantMessage()
      setSpriteState('thinking')

      break
    case 'message.delta': {
      const text = (event.payload as { text?: string } | undefined)?.text ?? ''

      if (text) {appendAssistantDelta(text)}

      break
    }

    case 'message.complete': {
      const payload = event.payload as { text?: string; affect?: { emotion?: string } } | undefined
      finalizeAssistantMessage(payload?.text)

      if (payload?.affect?.emotion) {
        setSpriteState('emotional', { emotion: payload.affect.emotion as SpriteEmotion })
      } else {
        setSpriteState('idle')
      }

      break
    }

    case 'affect': {
      const emotion = (event.payload as { emotion?: string } | undefined)?.emotion
      if (emotion) {
        setSpriteState('emotional', { emotion: emotion as SpriteEmotion })
      }
      break
    }

    case 'tool.call': {
      const p = (event.payload as { status?: string; name?: string } | undefined) ?? {}

      if (p.status === 'complete') {
        setAssistantTool(null)
        setSpriteState('thinking')
      } else {
        setAssistantTool(p.name ?? '工具')
        setSpriteState('working')
      }

      break
    }

    case 'video_gen.completed': {
      const payload = event.payload as { scene?: string; video_url?: string } | undefined
      if (payload?.scene) {
        setClipStatus(payload.scene, 'succeeded', payload.video_url ?? null)
      }
      break
    }

    case 'error': {
      const message = (event.payload as { message?: string } | undefined)?.message ?? '出了点小问题'
      setAssistantError(message)
      setSpriteState('idle')

      break
    }

    case 'cron.trigger':
      break
    case 'companion.message': {
      const payload = event.payload as { text?: string; affect?: { emotion?: string } } | undefined
      const text = payload?.text ?? ''
      const currentTier = $disturbanceTier.get()

      if (payload?.affect?.emotion) {
        setSpriteState('emotional', { emotion: payload.affect.emotion as SpriteEmotion })
      }

      if (text && currentTier !== 'quiet') {
        void speakProactive(text)
      }

      break
    }

    default:
      break
  }
}
