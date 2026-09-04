// 对话表面（消息流）：两个入口（生活空间、工作台）共用的中间栏组件。
//
// 仅负责把消息列表、typing 占位、流式滚动跟随三件事渲染出来；
// 头部拖拽、参数面板、会话侧栏、输入胶囊都归各自的宿主容器管理——
// 生活空间左栏 + 右栏布局、工作台三栏布局形态差异大，共用部分只此一处。

import { useStore } from '@nanostores/react'
import type React from 'react'
import { type RefObject, useEffect } from 'react'

import { type ConversationVariant, MessageBubble } from '@/chat/chat-dock-message-bubble'
import {
  $chatMessageList,
  $chatStreamingTick,
  $chatTurnInFlight,
  $lastAssistantStreaming,
  $pendingPromptBatch
} from '@/chat/chat-store'
import { $gatewayState } from '@/shared/store/gateway'

interface ConversationSurfaceProps {
  className?: string
  emptyHint?: string
  scrollRef: RefObject<HTMLDivElement | null>
  variant?: ConversationVariant
}

function AutoFollowScroll({ scrollRef }: { scrollRef: RefObject<HTMLDivElement | null> }): null {
  const tick = useStore($chatStreamingTick)

  useEffect(() => {
    const el = scrollRef.current

    if (!el) {
      return
    }

    el.scrollTo?.({ top: el.scrollHeight, behavior: 'smooth' })
  }, [tick, scrollRef])

  return null
}

export function ConversationSurface({
  className,
  emptyHint = '说点什么，或发送文件/图片/视频给我看看～',
  scrollRef,
  variant = 'living'
}: ConversationSurfaceProps): React.JSX.Element {
  const list = useStore($chatMessageList)
  const lastAssistantStreaming = useStore($lastAssistantStreaming)
  const chatTurnInFlight = useStore($chatTurnInFlight)
  const pendingPromptBatch = useStore($pendingPromptBatch)
  const gatewayState = useStore($gatewayState)

  const isTurnPendingOrInFlight = pendingPromptBatch.length > 0 || chatTurnInFlight
  const showTyping = isTurnPendingOrInFlight && !lastAssistantStreaming && gatewayState === 'open'

  return (
    <div className={className ?? 'flex-1 space-y-3 overflow-y-auto px-4 py-4'} data-surface={variant} ref={scrollRef}>
      {list.length === 0 && <p className="mt-8 text-center text-sm text-faint">{emptyHint}</p>}
      {list.map(item => (
        <MessageBubble key={item.id} message={item} variant={variant} />
      ))}
      {showTyping && (
        <div className="flex justify-start">
          <span className="rounded-2xl rounded-bl-sm border border-line-hairline bg-surface-card px-3.5 py-2.5 text-faint">
            …
          </span>
        </div>
      )}
      <AutoFollowScroll scrollRef={scrollRef} />
    </div>
  )
}
