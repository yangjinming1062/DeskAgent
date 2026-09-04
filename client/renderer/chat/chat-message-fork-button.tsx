import { useStore } from '@nanostores/react'
import { atom } from 'nanostores'
import type React from 'react'

import { $chatSessionId } from '@/chat/chat-store'
import { forkConversation } from '@/chat/session-list-store'
import { Copy, Loader2 } from '@/shared/lib/icons'
import { notifyError } from '@/shared/store/notifications'

interface ChatMessageForkButtonProps {
  /** 当前气泡的本地 id；同一时刻只允许一个 fork RPC 在飞，并发点击靠此 id 排他。 */
  messageId: string
  /** 后端 Message.id——直接回传给 session.fork 作为 source_message_id。 */
  sourceMessageId: number
  className?: string
}

const $chatForkInFlight = atom<string | null>(null)

export function ChatMessageForkButton({
  messageId,
  sourceMessageId,
  className = ''
}: ChatMessageForkButtonProps): React.JSX.Element {
  const sourceSessionId = useStore($chatSessionId)
  const inFlight = useStore($chatForkInFlight)

  const isMineInFlight = inFlight === messageId
  const otherBusy = inFlight !== null && !isMineInFlight

  const onClick = async (): Promise<void> => {
    if (!sourceSessionId || inFlight !== null) {
      return
    }

    $chatForkInFlight.set(messageId)

    try {
      // forkConversation 失败返回 null 时给用户一个具体提示——它内部只 log 不 toast
      const newId = await forkConversation(sourceSessionId, sourceMessageId)

      if (!newId) {
        notifyError(new Error('派生失败：服务端拒绝或网络异常'), '派生对话失败')
      }
    } catch (err) {
      notifyError(err, '派生对话失败')
    } finally {
      if ($chatForkInFlight.get() === messageId) {
        $chatForkInFlight.set(null)
      }
    }
  }

  let icon: React.JSX.Element
  let label: string
  let styleClass: string

  if (isMineInFlight) {
    icon = <Loader2 className="animate-spin" size={12} />
    label = '正在派生新对话…'
    styleClass =
      'rounded-full border border-line-strong bg-fill-hover px-2 py-1 text-xs text-strong transition hover:bg-fill-hover'
  } else if (otherBusy) {
    icon = <Copy size={12} />
    label = '另一条正在派生'
    styleClass =
      'rounded-full border border-line-standard bg-fill-faint px-2 py-1 text-xs text-faint cursor-not-allowed'
  } else {
    icon = <Copy size={12} />
    label = '从这条消息派生新对话'
    styleClass =
      'rounded-full border border-line-strong bg-fill-faint px-2 py-1 text-xs text-muted transition hover:bg-fill-hover hover:text-strong'
  }

  return (
    <button
      aria-label={label}
      className={`${styleClass} ${className} inline-flex items-center gap-1`}
      disabled={otherBusy || isMineInFlight}
      onClick={() => {
        void onClick()
      }}
      title={label}
      type="button"
    >
      {icon}
      <span>{label}</span>
    </button>
  )
}
