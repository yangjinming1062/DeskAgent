import { useStore } from '@nanostores/react'
import { useEffect, useRef } from 'react'

import { $chatMessageBodies, $chatMessageList } from '@/companion/chat-store'
import type { ChatMessageBody, ChatMessageListItem } from '@/companion/chat-store'
import { $subtitles } from '@/companion/prefs'

// 仅订阅最后一条消息的 body。条件渲染确保不会以空 keys 数组调用，避免 nanostores 回退为全量订阅。
function SubtitlesOverlayInner({ lastItem }: { lastItem: ChatMessageListItem }): React.JSX.Element | null {
  const bodies = useStore($chatMessageBodies, { keys: [lastItem.id], deps: [lastItem.id] })
  const body: ChatMessageBody | undefined = bodies[lastItem.id]
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    // 流式文本只增不删——始终滚到底部，让最新一句可见。
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [body?.text])

  if (!body) {
    return null
  }

  const isUser = lastItem.role === 'user'

  const displayText = body.error
    ? body.error
    : body.cancelled
      ? '已停止'
      : body.toolName
        ? `正在使用 ${body.toolName}…`
        : body.text || '…'

  return (
    <div className="flex h-full w-full select-none items-start gap-2 rounded-xl border border-white/8 bg-surface-card px-3 py-2 text-xs">
      <span className={`mt-[5px] h-1.5 w-1.5 shrink-0 rounded-full ${isUser ? 'bg-emerald-400' : 'bg-blue-400'}`} />
      <span className="mt-0.5 shrink-0 font-medium text-white/50">{isUser ? '用户:' : '伙伴:'}</span>
      <div
        className="min-h-0 flex-1 self-stretch overflow-y-auto break-words leading-relaxed text-white/90 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        ref={scrollRef}
      >
        {displayText}
      </div>
    </div>
  )
}

// 通话面板内嵌字幕条（DESIGN §6.1「双向字幕可切换」）：跟随面板本体渲染；
// 关闭或尚无消息时占位，保持面板高度稳定。
export function SubtitlesOverlay(): React.JSX.Element {
  const userPref = useStore($subtitles)
  const list = useStore($chatMessageList)
  const lastItem = list[list.length - 1]

  if (!userPref || !lastItem) {
    return (
      <div className="flex h-full w-full select-none items-center justify-center rounded-xl border border-dashed border-white/10 text-xs text-white/25">
        {!userPref ? '字幕已关闭' : '等待对话…'}
      </div>
    )
  }

  return <SubtitlesOverlayInner lastItem={lastItem} />
}
