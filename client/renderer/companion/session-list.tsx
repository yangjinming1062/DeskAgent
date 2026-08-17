import { useStore } from '@nanostores/react'
import type React from 'react'
import { useRef } from 'react'

import { usePanelDrag } from '@/companion/hooks/use-panel-drag'
import { useInteractiveRegion } from '@/companion/interactive-regions'

import { $chatSessionId } from './chat-store'
import { $sessions, $sessionsLoading, createNewSession, deleteSession, switchSession } from './session-list-store'

export function SessionListPanel({ onClose }: { onClose: () => void }): React.ReactElement {
  const sessions = useStore($sessions)
  const loading = useStore($sessionsLoading)
  const activeSessionId = useStore($chatSessionId)
  const panelRef = useRef<HTMLDivElement>(null)

  useInteractiveRegion('session-list', panelRef)
  const { bind: dragBind, storedOffset } = usePanelDrag('da.companion.sessionListOffset', () => panelRef.current)

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
    // The companion window is click-through except for registered interactive
    // regions, so the wrapper must not paint or capture — only `panelRef` does.
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ pointerEvents: 'none' }}>
      <div
        className="w-full max-w-md rounded-2xl border border-white/15 bg-gray-900/90 p-4 text-white shadow-2xl backdrop-blur-xl"
        ref={panelRef}
        style={{
          pointerEvents: 'auto',
          transform: storedOffset ? `translate3d(${storedOffset.dx}px, ${storedOffset.dy}px, 0)` : undefined
        }}
      >
        <div
          className="flex cursor-grab items-center justify-between border-b border-white/10 pb-3 active:cursor-grabbing"
          {...dragBind}
          title="拖动以移动面板"
        >
          <div className="flex items-center gap-2 font-medium">
            <span>💬 对话列表</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              className="flex items-center gap-1 rounded-lg bg-indigo-600/80 px-2.5 py-1 text-xs font-medium text-white transition hover:bg-indigo-500"
              onClick={() => void handleCreate()}
              type="button"
            >
              <span>+ 新建对话</span>
            </button>
            <button
              className="rounded-lg p-1 text-white/60 transition hover:bg-white/10 hover:text-white"
              onClick={onClose}
              type="button"
            >
              ✕
            </button>
          </div>
        </div>

        <div className="mt-3 max-h-80 space-y-2 overflow-y-auto pr-1">
          {loading ? (
            <div className="py-8 text-center text-xs text-white/50">加载中…</div>
          ) : sessions.length === 0 ? (
            <div className="py-8 text-center text-xs text-white/50">暂无对话记录</div>
          ) : (
            sessions.map(s => {
              const isMain = s.kind === 'main'
              const isActive = s.id === activeSessionId

              return (
                <div
                  className={`group relative flex cursor-pointer items-center justify-between rounded-xl border p-3 transition ${
                    isActive
                      ? 'border-indigo-500/50 bg-indigo-500/15'
                      : 'border-white/10 bg-white/5 hover:border-white/20 hover:bg-white/10'
                  }`}
                  key={s.id}
                  onClick={() => void handleSwitch(s.id)}
                >
                  <div className="min-w-0 flex-1 pr-2">
                    <div className="flex items-center gap-1.5 text-sm font-medium text-white/90">
                      {isMain ? <span className="text-amber-400">🏠</span> : <span className="text-white/40">💬</span>}
                      <span className="truncate">{s.title || (isMain ? '日常对话' : '新建对话')}</span>
                    </div>
                    {s.preview && <p className="mt-1 truncate text-xs text-white/50">{s.preview}</p>}
                  </div>

                  <div className="flex items-center gap-2">
                    {!isMain && (
                      <button
                        className="rounded p-1 text-xs text-red-400 opacity-0 transition hover:bg-red-500/20 group-hover:opacity-100"
                        onClick={e => void handleDelete(e, s.id)}
                        title="删除对话"
                        type="button"
                      >
                        🗑️
                      </button>
                    )}
                  </div>
                </div>
              )
            })
          )}
        </div>
      </div>
    </div>
  )
}
