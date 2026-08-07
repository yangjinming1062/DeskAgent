import { useStore } from '@nanostores/react'

import { $chatMessages } from '@/companion/chat-store'

interface SubtitlesOverlayProps {
  visible?: boolean
}

export function SubtitlesOverlay({ visible = true }: SubtitlesOverlayProps) {
  const messages = useStore($chatMessages)
  const lastMessage = messages[messages.length - 1]

  if (!visible || !lastMessage) {
    return null
  }

  const isUser = lastMessage.role === 'user'

  return (
    <div className="pointer-events-none fixed bottom-6 left-1/2 z-50 -translate-x-1/2 px-4 select-none">
      <div className="flex max-w-md items-center gap-2 rounded-full border border-white/15 bg-black/70 px-4 py-2 text-xs text-white shadow-xl backdrop-blur-md transition-all duration-300">
        <span className={`h-2 w-2 rounded-full shrink-0 ${isUser ? 'bg-emerald-400' : 'bg-blue-400'}`} />
        <span className="font-medium text-white/50">{isUser ? '用户:' : '伙伴:'}</span>
        <span className="truncate text-white/90">{lastMessage.text || '…'}</span>
      </div>
    </div>
  )
}
