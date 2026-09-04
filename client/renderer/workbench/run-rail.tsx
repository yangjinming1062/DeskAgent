// 工作台 Run Rail：本轮工具调用、最新的终端片段 / diff / 截图 / 生成媒体、本会话工件。
//
// 一期按计划 §4.4「纯 Client 投影」实现：读 chat-store 派生；后续工具进程结果回流后
// 直接在 chip 旁展开摘要/媒体。

import { useStore } from '@nanostores/react'
import type React from 'react'

import { $chatMessageBodies, $chatMessageList, type ChatMessageBody } from '@/chat/chat-store'

export function RunRail(): React.JSX.Element {
  const list = useStore($chatMessageList)
  const bodies = useStore($chatMessageBodies)

  // 从尾部扫第一条 assistant 消息，避免 list.reverse() 拷一份 + 反转。
  let lastBody: ChatMessageBody | undefined

  for (let i = list.length - 1; i >= 0; i--) {
    if (list[i].role === 'assistant') {
      lastBody = bodies[list[i].id]

      break
    }
  }

  // 与 MessageBubble 里的 ToolTimeline 同步：单条 toolName 也算一轮。
  const tools = lastBody?.tools?.length ? lastBody.tools : lastBody?.toolName ? [lastBody.toolName] : []

  const lastToolName = lastBody?.toolName ?? null

  return (
    <aside className="flex w-80 shrink-0 flex-col border-l border-line-standard bg-surface-chrome">
      <div className="flex items-center justify-between gap-2 px-3 pb-2 pt-3">
        <h3 className="text-xs font-semibold text-body">本轮</h3>
        <span className="text-[10px] text-faint">{tools.length} 步</span>
      </div>

      <div className="space-y-1 px-3 pb-3">
        {tools.length === 0 ? (
          <p className="py-6 text-center text-xs text-faint">还没有工具调用</p>
        ) : (
          tools.map((name, index) => (
            <div
              className={`flex items-center gap-2 rounded-md border px-2 py-1 text-[11px] ${
                index === tools.length - 1 && lastToolName === name
                  ? 'border-accent-line bg-accent-soft text-accent'
                  : 'border-line-standard text-muted'
              }`}
              key={`${name}-${index}`}
            >
              <span className="font-mono">{name}</span>
            </div>
          ))
        )}
      </div>

      <div className="border-t border-line-standard px-3 py-2">
        <h4 className="text-[10px] uppercase tracking-wider text-faint">最新输出</h4>
        <p className="mt-1 text-xs text-muted">终端片段、diff 摘要与媒体将在 C2 后续小节接入。</p>
      </div>
    </aside>
  )
}
