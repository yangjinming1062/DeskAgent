import { useStore } from '@nanostores/react'
import { atom } from 'nanostores'
import type React from 'react'

import { $chatSessionId } from '@/companion/chat-store'
import { undoToMessage } from '@/companion/session-list-store'
import { ArrowBackUp, Loader2 } from '@/shared/lib/icons'
import { notifyError } from '@/shared/store/notifications'

interface ChatMessageUndoButtonProps {
  /** 当前气泡的本地 id；同一时刻只允许一个 undo RPC 在飞，靠此 id 排他。 */
  messageId: string
  /** 后端 Message.id——直接回传给 session.undo_to_message 作为 source_message_id。 */
  sourceMessageId: number
  className?: string
}

const $chatUndoInFlight = atom<string | null>(null)

export function ChatMessageUndoButton({
  messageId,
  sourceMessageId,
  className = ''
}: ChatMessageUndoButtonProps): React.JSX.Element {
  const sourceSessionId = useStore($chatSessionId)
  const inFlight = useStore($chatUndoInFlight)

  const isMineInFlight = inFlight === messageId
  const otherBusy = inFlight !== null && !isMineInFlight

  const onClick = async (): Promise<void> => {
    if (!sourceSessionId || inFlight !== null) {
      return
    }

    if (!window.confirm('撤回这条消息？此操作将一并删除其后所有消息。')) {
      return
    }

    $chatUndoInFlight.set(messageId)

    try {
      const ok = await undoToMessage(sourceSessionId, sourceMessageId)

      if (!ok) {
        notifyError(new Error('撤回失败：服务端拒绝或网络异常'), '撤回消息失败')
      }
    } catch (err) {
      notifyError(err, '撤回消息失败')
    } finally {
      if ($chatUndoInFlight.get() === messageId) {
        $chatUndoInFlight.set(null)
      }
    }
  }

  let icon: React.JSX.Element
  let label: string
  let styleClass: string

  if (isMineInFlight) {
    icon = <Loader2 className="animate-spin" size={12} />
    label = '正在撤回…'
    styleClass =
      'rounded-md border border-line-strong bg-fill-hover px-2 py-1 text-xs text-strong transition hover:bg-fill-hover'
  } else if (otherBusy) {
    icon = <ArrowBackUp size={12} />
    label = '另一条正在撤回'
    styleClass = 'rounded-md border border-line-standard bg-fill-faint px-2 py-1 text-xs text-faint cursor-not-allowed'
  } else {
    icon = <ArrowBackUp size={12} />
    label = '撤回此条消息'
    styleClass =
      'rounded-md border border-line-strong bg-fill-faint px-2 py-1 text-xs text-muted transition hover:bg-fill-hover hover:text-strong'
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
