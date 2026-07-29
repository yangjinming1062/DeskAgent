// The companion's WS event dispatcher — the designated graft point that was
// empty in use-gateway-boot's handleGatewayEvent. Maps streaming chat frames
// (message.*, tool.call) onto the chat store + the MVP state machine subset
// (plan §2). Proactive events (cron.trigger / companion.message) and affect
// arrive in later slices.
import type { RpcEvent } from '@/types/deskagent'
import {
  appendAssistantDelta,
  beginAssistantMessage,
  finalizeAssistantMessage,
  setAssistantError,
  setAssistantTool
} from '@/store/chat'
import { setSpriteState } from '@/store/companion'

export function handleCompanionEvent(event: RpcEvent): void {
  switch (event.type) {
    case 'message.start':
      beginAssistantMessage()
      setSpriteState('thinking')
      break

    case 'message.delta': {
      const text = (event.payload as { text?: string } | undefined)?.text ?? ''
      if (text) appendAssistantDelta(text)
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
      // Slice 4: proactive companionship driven by cron.
      break

    default:
      break
  }
}
