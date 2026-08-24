import { useStore } from '@nanostores/react'

import { $chatMessageBodies, $chatMessageList } from '@/companion/chat-store'
import type { ChatMessageBody, ChatMessageListItem } from '@/companion/chat-store'
import { $subtitles } from '@/companion/prefs'

interface SubtitlesOverlayProps {
  visible?: boolean
}

// 仅订阅最后一条消息的 body。条件渲染确保不会以空 keys 数组调用，避免 nanostores 回退为全量订阅。
function SubtitlesOverlayInner({ lastItem }: { lastItem: ChatMessageListItem }): React.JSX.Element | null {
  const bodies = useStore($chatMessageBodies, { keys: [lastItem.id], deps: [lastItem.id] })
  const body: ChatMessageBody | undefined = bodies[lastItem.id]

  if (!body) {
    return null
  }

  const isUser = lastItem.role === 'user'

  const displayText = body.error
    ? `😬 ${body.error}`
    : body.cancelled
      ? '已停止'
      : body.toolName
        ? `🔧 正在使用 ${body.toolName}…`
        : body.text || '…'

  return (
    <div className="pointer-events-none fixed bottom-6 left-1/2 z-50 -translate-x-1/2 px-4 select-none">
      <div className="flex max-w-md items-center gap-2 rounded-full border border-white/15 bg-black/70 px-4 py-2 text-xs text-white shadow-xl backdrop-blur-md transition-all duration-300">
        <span className={`h-2 w-2 rounded-full shrink-0 ${isUser ? 'bg-emerald-400' : 'bg-blue-400'}`} />
        <span className="font-medium text-white/50">{isUser ? '用户:' : '伙伴:'}</span>
        <span className="truncate text-white/90">{displayText}</span>
      </div>
    </div>
  )
}

export function SubtitlesOverlay({ visible = true }: SubtitlesOverlayProps): React.JSX.Element | null {
  // DESIGN §6.1「双向字幕可切换」：尊重全局持久化偏好 $subtitles。
  const userPref = useStore($subtitles)
  const list = useStore($chatMessageList)
  const lastItem = list[list.length - 1]

  if (!visible || !userPref || !lastItem) {
    return null
  }

  return <SubtitlesOverlayInner lastItem={lastItem} />
}
