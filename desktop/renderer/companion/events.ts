import {
  appendAssistantDelta,
  beginAssistantMessage,
  finalizeAssistantMessage,
  setAssistantError,
  setAssistantTool
} from '@/companion/chat-store'
import { setSpriteState } from '@/companion/companion-store'
// The companion's WS event dispatcher — the designated graft point that was
// empty in use-gateway-boot's handleGatewayEvent. Maps streaming chat frames
// (message.*, tool.call) onto the chat store + the MVP state machine subset
// (plan §2). Proactive events (cron.trigger / companion.message) and affect
// arrive in later slices.
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

    case 'message.complete':
      finalizeAssistantMessage((event.payload as { text?: string } | undefined)?.text)
      setSpriteState('idle')

      break
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

    case 'error': {
      const message = (event.payload as { message?: string } | undefined)?.message ?? '出了点小问题'
      setAssistantError(message)
      setSpriteState('idle')

      break
    }

    case 'cron.trigger':
      // Backend (ARCHITECTURE.md §6) processes cron into a `companion.message`; the
      // desktop doesn't run the cron turn itself. No-op until that lands.
      break
    case 'companion.message': {
      const text = (event.payload as { text?: string } | undefined)?.text ?? ''

      if (text) {void speakProactive(text)}

      break
    }

    default:
      break
  }
}
