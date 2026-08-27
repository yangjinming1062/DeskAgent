import { useStore } from '@nanostores/react'
import type React from 'react'

import { $chatSessionId } from '@/companion/chat-store'
import {
  $sessions,
  $sessionsLoading,
  createNewSession,
  deleteSession,
  switchSession
} from '@/companion/session-list-store'
import { Home, MessageCircle, Plus, Trash2 } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { BTN_PRIMARY } from '@/shared/panel'

// 聊天窗内左侧会话抽屉：切换 / 新建 / 删除历史对话（原居中弹层的抽屉化形态）。
// 选中或新建后由 ChatDock 关闭抽屉。
export function SessionDrawer({ onClose }: { onClose: () => void }): React.JSX.Element {
  const sessions = useStore($sessions)
  const loading = useStore($sessionsLoading)
  const activeSessionId = useStore($chatSessionId)

  const handleCreate = async (): Promise<void> => {
    await createNewSession()
    onClose()
  }

  const handleSwitch = async (id: string): Promise<void> => {
    await switchSession(id)
    onClose()
  }

  const handleDelete = async (e: React.MouseEvent, id: string): Promise<void> => {
    e.stopPropagation()
    await deleteSession(id)
  }

  return (
    <aside className="sa-drawer-in flex w-64 shrink-0 flex-col border-r border-white/10 bg-[#0f0f11]">
      <div className="flex items-center justify-between gap-2 px-3 pb-2 pt-3">
        <h3 className="text-xs font-semibold text-white/80">对话</h3>
        <button
          className={cn(BTN_PRIMARY, 'h-6 gap-1 px-2 text-[11px]')}
          onClick={() => void handleCreate()}
          type="button"
        >
          <Plus className="size-3" />
          新建
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
        {loading ? (
          <div className="py-8 text-center text-xs text-white/40">加载中…</div>
        ) : sessions.length === 0 ? (
          <div className="py-8 text-center text-xs text-white/40">暂无对话记录</div>
        ) : (
          sessions.map(s => {
            const isMain = s.kind === 'main'
            const isActive = s.id === activeSessionId

            return (
              <div
                className={`group flex cursor-pointer items-center gap-2 rounded-lg border px-2.5 py-2 transition ${
                  isActive
                    ? 'border-[#6c8aff]/50 bg-[#6c8aff]/10'
                    : 'border-transparent hover:border-white/10 hover:bg-white/5'
                }`}
                key={s.id}
                onClick={() => void handleSwitch(s.id)}
              >
                {isMain ? (
                  <Home className="size-3.5 shrink-0 text-amber-300/80" />
                ) : (
                  <MessageCircle className="size-3.5 shrink-0 text-white/35" />
                )}
                <div className="min-w-0 flex-1">
                  <p className={cn('truncate text-xs', isActive ? 'font-medium text-white' : 'text-white/85')}>
                    {s.title || (isMain ? '日常对话' : '新建对话')}
                  </p>
                  {s.preview && <p className="mt-0.5 truncate text-[10px] text-white/35">{s.preview}</p>}
                </div>
                {!isMain && (
                  <button
                    aria-label="删除对话"
                    className="shrink-0 rounded-md p-1 text-white/35 opacity-0 transition hover:bg-rose-500/15 hover:text-rose-300 group-hover:opacity-100"
                    onClick={e => void handleDelete(e, s.id)}
                    title="删除对话"
                    type="button"
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                )}
              </div>
            )
          })
        )}
      </div>
    </aside>
  )
}
