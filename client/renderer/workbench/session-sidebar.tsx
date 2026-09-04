import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { $chatSessionId } from '@/chat/chat-store'
import { PresetIconBadge, PresetPickerModal } from '@/chat/preset-picker-modal'
import {
  $archivedLoading,
  $archivedSessions,
  $archiveOpen,
  $searchLoading,
  $searchResults,
  $sessions,
  $sessionSearch,
  $sessionsLoading,
  $sessionSort,
  $systemPresets,
  $systemPresetsFetched,
  $systemPresetsLoading,
  archiveSession,
  createNewSession,
  deleteSession,
  fetchSystemPresets,
  isCompanionSession,
  pinSession,
  renameSession,
  runSessionSearch,
  type SessionSort,
  setSessionSort,
  switchSession,
  TITLE_MAX_CHARS
} from '@/chat/session-list-store'
import {
  Archive,
  ArchiveOff,
  ArrowRight,
  CalendarPlus,
  Clock,
  Cpu,
  Globe,
  type IconComponent,
  List,
  Messages,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Trash2
} from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { INPUT_CLASS, SearchField } from '@/shared/panel'
import { strings } from '@/shared/strings'
import type { SessionInfo } from '@/shared/types/spiritagent'

const SORT_OPTIONS: { icon: IconComponent; label: string; value: SessionSort }[] = [
  { icon: Clock, label: '按最近活跃排序', value: 'recent' },
  { icon: CalendarPlus, label: '按创建时间排序', value: 'created' },
  { icon: Messages, label: '按消息数排序', value: 'messages' }
]

const WORKBENCH_PRESET_META: Record<string, { icon: IconComponent; label: string }> = {
  copywriter: { icon: Pencil, label: '文案秘书' },
  developer: { icon: Cpu, label: '开发工程师' },
  language_teacher: { icon: Globe, label: '语言老师' },
  product_manager: { icon: List, label: '产品经理' }
}

function formatSessionTime(timestamp?: number): string {
  if (!timestamp) {
    return '刚刚'
  }

  const date = new Date(timestamp > 1e11 ? timestamp : timestamp * 1000)
  const now = new Date()
  const isToday = date.toDateString() === now.toDateString()
  const timeStr = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

  if (isToday) {
    return `今天 ${timeStr}`
  }

  const yesterday = new Date(now)
  yesterday.setDate(yesterday.getDate() - 1)

  if (date.toDateString() === yesterday.toDateString()) {
    return `昨天 ${timeStr}`
  }

  return `${date.getMonth() + 1}月${date.getDate()}日 ${timeStr}`
}

export function SessionSidebar(): React.JSX.Element {
  const sessions = useStore($sessions)
  const loading = useStore($sessionsLoading)
  const sort = useStore($sessionSort)
  const search = useStore($sessionSearch)
  const searchResults = useStore($searchResults)
  const searchLoading = useStore($searchLoading)
  const archivedSessions = useStore($archivedSessions)
  const archivedLoading = useStore($archivedLoading)
  const archiveOpen = useStore($archiveOpen)
  const activeSessionId = useStore($chatSessionId)
  const presets = useStore($systemPresets)
  const presetsLoading = useStore($systemPresetsLoading)
  const presetsFetched = useStore($systemPresetsFetched)
  const [pickerOpen, setPickerOpen] = useState(false)

  const searchActive = search.trim().length > 0

  useEffect(() => {
    const q = search.trim()

    if (!q) {
      void runSessionSearch('')

      return
    }

    const timer = setTimeout(() => void runSessionSearch(q), 300)

    return () => clearTimeout(timer)
  }, [search])

  useEffect(() => {
    if (!presetsFetched) {
      void fetchSystemPresets()
    }
  }, [presetsFetched])

  const handleCreate = (): void => {
    setPickerOpen(true)
  }

  const handlePickerConfirm = async (presetId: string): Promise<void> => {
    setPickerOpen(false)
    await createNewSession(presetId)
  }

  const handleSwitch = async (id: string): Promise<void> => {
    await switchSession(id)
  }

  const isSpecialSession = (s: SessionInfo): boolean => s.kind === 'special'

  const workbenchSessions = sessions.filter(s => !isCompanionSession(s))

  // 工作台 4 套专业系统预设
  const specialSessions = workbenchSessions
    .filter(s => isSpecialSession(s))
    .sort((a, b) => {
      const order = ['developer', 'product_manager', 'copywriter', 'language_teacher']
      const aIdx = a.system_preset_id ? order.indexOf(a.system_preset_id) : 99
      const bIdx = b.system_preset_id ? order.indexOf(b.system_preset_id) : 99

      return aIdx - bIdx
    })

  // 用户常规会话（支持置顶与非置顶）
  const pinnedRegularSessions = workbenchSessions.filter(s => !isSpecialSession(s) && s.pinned)
  const unpinnedRegularSessions = workbenchSessions.filter(s => !isSpecialSession(s) && !s.pinned)

  // 过滤掉弹出框中的 companion
  const nonCompanionPresets = presets.filter(p => p.id !== 'companion')

  return (
    <aside className="flex h-full w-full min-h-0 flex-col overflow-hidden text-xs">
      {/* 顶部操作：Sessions 标题与 + 新建按钮 */}
      <div className="flex items-center justify-between px-3.5 pt-3 pb-1">
        <h3 className="text-sm font-semibold tracking-wide text-white/90">Sessions</h3>
        <button
          aria-label="新建对话"
          className="flex size-6 items-center justify-center rounded-lg border border-white/12 bg-white/5 text-white/80 transition hover:border-white/25 hover:bg-white/15 hover:text-white active:scale-95"
          onClick={handleCreate}
          title="新建对话"
          type="button"
        >
          <Plus className="size-3.5" />
        </button>
      </div>

      {/* 搜索与排序 */}
      <div className="flex items-center justify-between gap-1.5 px-3 py-1.5">
        <div className="min-w-0 flex-1">
          <SearchField ariaLabel="搜索会话" onChange={$sessionSearch.set} placeholder="搜索会话…" value={search} />
        </div>
        <div className="flex items-center gap-0.5 shrink-0">
          {SORT_OPTIONS.map(({ icon: Icon, label, value }) => (
            <button
              aria-label={label}
              className={cn(
                'rounded-lg p-1.5 transition-colors',
                value === sort ? 'bg-white/15 text-strong shadow-xs' : 'text-muted hover:bg-white/8 hover:text-strong'
              )}
              key={value}
              onClick={() => setSessionSort(value)}
              title={label}
              type="button"
            >
              <Icon className="size-3.5" />
            </button>
          ))}
        </div>
      </div>

      {/* 会话列表区域 */}
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-2.5 pb-3 scrollbar-thin">
        {searchActive ? (
          <div>
            <div className="mb-1 px-1.5 text-[11px] font-medium text-muted">搜索结果</div>
            {searchLoading ? (
              <div className="py-6 text-center text-muted">搜索中…</div>
            ) : searchResults.filter(s => !isCompanionSession(s)).length === 0 ? (
              <div className="py-6 text-center text-muted">没有找到匹配会话</div>
            ) : (
              searchResults
                .filter(s => !isCompanionSession(s))
                .map(s => (
                  <SessionRow
                    badge={s.archived ? '已归档' : undefined}
                    isActive={s.id === activeSessionId}
                    key={s.id}
                    onSwitch={handleSwitch}
                    session={s}
                  />
                ))
            )}
          </div>
        ) : (
          <>
            {/* 4 个专业工作预设 */}
            <div>
              <div className="mb-1 flex items-center justify-between px-1.5 text-[11px] font-semibold text-muted tracking-wider">
                <span>专业工作预设</span>
                <span className="rounded bg-white/10 px-1 py-0.2 text-[10px] text-muted">4</span>
              </div>
              <div className="space-y-0.5">
                {specialSessions.map(s => {
                  const meta = s.system_preset_id ? WORKBENCH_PRESET_META[s.system_preset_id] : undefined
                  const Icon = meta?.icon ?? Cpu

                  return (
                    <SessionRow
                      customIcon={Icon}
                      customLabel={meta?.label}
                      isActive={s.id === activeSessionId}
                      isSpecial
                      key={s.id}
                      onSwitch={handleSwitch}
                      session={s}
                    />
                  )
                })}
              </div>
            </div>

            {/* 用户自定义会话 */}
            <div>
              <div className="mb-1 flex items-center justify-between px-1.5 text-[11px] font-semibold text-muted tracking-wider">
                <span>自定义会话</span>
                {pinnedRegularSessions.length + unpinnedRegularSessions.length > 0 && (
                  <span className="text-[10px] text-muted">
                    {pinnedRegularSessions.length + unpinnedRegularSessions.length}
                  </span>
                )}
              </div>

              {loading && workbenchSessions.length === 0 ? (
                <div className="py-4 text-center text-muted">加载会话中…</div>
              ) : pinnedRegularSessions.length === 0 && unpinnedRegularSessions.length === 0 ? (
                <div className="rounded-xl border border-dashed border-white/10 p-4 text-center text-muted">
                  暂无自建会话，点击上方新建
                </div>
              ) : (
                <div className="space-y-0.5">
                  {pinnedRegularSessions.map(s => (
                    <SessionRow
                      actions={
                        <>
                          <RowAction
                            icon={PinOff}
                            label="取消置顶"
                            onClick={e => stopThen(e, () => void pinSession(s.id, false))}
                          />
                          <RowAction
                            icon={Archive}
                            label="归档"
                            onClick={e => stopThen(e, () => void archiveSession(s.id, true))}
                          />
                        </>
                      }
                      isActive={s.id === activeSessionId}
                      key={s.id}
                      onSwitch={handleSwitch}
                      session={s}
                    />
                  ))}
                  {pinnedRegularSessions.length > 0 && unpinnedRegularSessions.length > 0 && (
                    <div className="my-1 border-t border-line-hairline opacity-40" />
                  )}
                  {unpinnedRegularSessions.map(s => (
                    <SessionRow
                      actions={
                        <>
                          <RowAction
                            icon={Pin}
                            label="置顶"
                            onClick={e => stopThen(e, () => void pinSession(s.id, true))}
                          />
                          <RowAction
                            icon={Archive}
                            label="归档"
                            onClick={e => stopThen(e, () => void archiveSession(s.id, true))}
                          />
                          <RowAction
                            danger
                            icon={Trash2}
                            label="删除"
                            onClick={e => stopThen(e, () => void deleteSession(s.id))}
                          />
                        </>
                      }
                      isActive={s.id === activeSessionId}
                      key={s.id}
                      onSwitch={handleSwitch}
                      session={s}
                    />
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>

      {/* 底部归档与展开切换 */}
      <div className="border-t border-white/8 bg-surface-panel/20 p-2.5">
        <button
          className="flex w-full items-center justify-center gap-1.5 rounded-xl border border-white/10 bg-white/5 py-1.5 text-xs text-white/75 transition hover:border-white/20 hover:bg-white/10 hover:text-white active:scale-98"
          onClick={() => $archiveOpen.set(!archiveOpen)}
          type="button"
        >
          <span>
            {archiveOpen
              ? '收起归档会话'
              : `已归档会话${archivedSessions.length > 0 ? ` (${archivedSessions.length})` : ''}`}
          </span>
          <ArrowRight className="size-3" />
        </button>
        {archiveOpen && (
          <div className="mt-2 max-h-48 space-y-1 overflow-y-auto pr-0.5">
            {archivedLoading ? (
              <div className="py-2 text-center text-muted">加载中…</div>
            ) : archivedSessions.length === 0 ? (
              <div className="py-2 text-center text-muted">暂无归档会话</div>
            ) : (
              archivedSessions.map(s => (
                <SessionRow
                  actions={
                    <>
                      <RowAction
                        icon={ArchiveOff}
                        label="恢复"
                        onClick={e => stopThen(e, () => void archiveSession(s.id, false))}
                      />
                      <RowAction
                        danger
                        icon={Trash2}
                        label="删除"
                        onClick={e => stopThen(e, () => void deleteSession(s.id))}
                      />
                    </>
                  }
                  isActive={s.id === activeSessionId}
                  key={s.id}
                  onSwitch={handleSwitch}
                  session={s}
                />
              ))
            )}
          </div>
        )}
      </div>

      {pickerOpen && (
        <PresetPickerModal
          loading={presetsLoading}
          onClose={() => setPickerOpen(false)}
          onConfirm={handlePickerConfirm}
          presets={nonCompanionPresets}
        />
      )}
    </aside>
  )
}

function stopThen(e: React.MouseEvent, action: () => void): void {
  e.preventDefault()
  e.stopPropagation()
  action()
}

function SessionRow({
  session,
  isActive,
  isSpecial,
  customIcon: CustomIcon,
  customLabel,
  actions,
  badge,
  onSwitch
}: {
  session: SessionInfo
  isActive: boolean
  isSpecial?: boolean
  customIcon?: IconComponent
  customLabel?: string
  actions?: React.ReactNode
  badge?: string
  onSwitch: (id: string) => Promise<void> | void
}): React.JSX.Element {
  const canRename = !isSpecial && session.kind !== 'im'
  const [editing, setEditing] = useState(false)
  const presets = useStore($systemPresets)

  const presetName =
    customLabel ?? (session.system_preset_id ? presets.find(p => p.id === session.system_preset_id)?.name : undefined)

  const title = customLabel ?? session.title ?? (isSpecial ? (presetName ?? '专业工位') : '新对话')
  const timeStr = formatSessionTime(session.last_active || session.started_at)
  const msgCount = session.message_count ?? 0

  return (
    <div
      className={cn(
        'group relative flex cursor-pointer flex-col gap-1 rounded-xl border p-2.5 transition-all duration-150 select-none',
        isActive
          ? 'border-blue-500/40 bg-blue-950/40 text-strong shadow-[inset_0_1px_1px_rgba(255,255,255,0.15),0_4px_16px_rgba(0,0,0,0.3)] backdrop-blur-md'
          : 'border-white/8 bg-white/[0.03] text-muted hover:border-white/15 hover:bg-white/[0.07] hover:text-strong'
      )}
      onClick={() => {
        if (!editing) {
          void onSwitch(session.id)
        }
      }}
    >
      <div className="flex items-center justify-between gap-1.5">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          {isActive ? (
            <span className="size-2 shrink-0 rounded-full bg-blue-500 shadow-[0_0_8px_rgba(59,130,246,0.9)]" />
          ) : CustomIcon ? (
            <CustomIcon className="size-3.5 shrink-0 text-faint" />
          ) : isSpecial ? (
            <span className="shrink-0" title={presetName ?? ''}>
              <PresetIconBadge iconKey={session.system_preset_icon_key} />
            </span>
          ) : (
            <span className="size-1.5 shrink-0 rounded-full bg-white/20" />
          )}

          {editing ? (
            <SessionTitleInput
              initialTitle={session.title ?? ''}
              onCancel={() => setEditing(false)}
              onCommit={title => {
                setEditing(false)
                void renameSession(session.id, title)
              }}
            />
          ) : (
            <p className={cn('truncate text-xs font-medium', isActive ? 'font-semibold text-white' : 'text-white/85')}>
              {title}
              {session.pinned && <Pin className="ml-1 inline size-2.5 text-accent opacity-75" />}
            </p>
          )}
        </div>

        <div className="flex items-center gap-0.5 shrink-0" onClick={e => e.stopPropagation()}>
          {canRename && !editing && (
            <div className="opacity-0 transition-opacity group-hover:opacity-100">
              <RowAction
                icon={Pencil}
                label={strings.chat.sessionRename.action}
                onClick={e => stopThen(e, () => setEditing(true))}
              />
            </div>
          )}
          <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
            {actions}
          </div>
          {badge && <span className="rounded bg-white/10 px-1 py-0.2 text-[9px] text-muted">{badge}</span>}
        </div>
      </div>

      <div className="flex items-center justify-between pl-4 text-[10.5px] text-white/45">
        <span>{timeStr}</span>
        <span>{msgCount} 条消息</span>
      </div>
    </div>
  )
}

function SessionTitleInput({
  initialTitle,
  onCommit,
  onCancel
}: {
  initialTitle: string
  onCommit: (title: string) => void
  onCancel: () => void
}): React.JSX.Element {
  const [draft, setDraft] = useState(initialTitle)
  const doneRef = useRef(false)

  const finish = (commit: boolean): void => {
    if (doneRef.current) {
      return
    }

    doneRef.current = true

    if (commit) {
      onCommit(draft)
    } else {
      onCancel()
    }
  }

  return (
    <input
      aria-label={strings.chat.sessionRename.inputLabel}
      autoFocus
      className={cn(INPUT_CLASS, 'h-6 px-1.5 py-0 text-xs')}
      maxLength={TITLE_MAX_CHARS}
      onBlur={() => finish(true)}
      onChange={e => setDraft(e.target.value)}
      onClick={e => e.stopPropagation()}
      onFocus={e => e.currentTarget.select()}
      onKeyDown={e => {
        if (e.key === 'Enter' || e.key === 'Escape') {
          e.preventDefault()
          e.stopPropagation()
          finish(e.key === 'Enter')
        }
      }}
      placeholder={strings.chat.sessionRename.placeholder}
      title={strings.chat.sessionRename.hint}
      value={draft}
    />
  )
}

function RowAction({
  icon: Icon,
  label,
  danger,
  onClick
}: {
  icon: IconComponent
  label: string
  danger?: boolean
  onClick: (e: React.MouseEvent) => void
}): React.JSX.Element {
  return (
    <button
      aria-label={label}
      className={cn(
        'rounded-md p-1 transition-colors',
        danger
          ? 'text-faint hover:bg-rose-500/20 hover:text-rose-300'
          : 'text-faint hover:bg-white/10 hover:text-strong'
      )}
      onClick={onClick}
      title={label}
      type="button"
    >
      <Icon className="size-3" />
    </button>
  )
}
