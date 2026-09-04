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
  CalendarPlus,
  ChevronDown,
  Clock,
  Cpu,
  Globe,
  type IconComponent,
  List,
  MessageCircle,
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
      {/* 顶部操作：+ 新建对话 与 排序 */}
      <div className="flex flex-col gap-2 p-3 pb-2">
        <button
          className="flex h-8 w-full items-center justify-center gap-2 rounded-xl border border-white/20 bg-white/10 px-3 text-xs font-semibold text-strong shadow-sm backdrop-blur-md transition-all duration-150 hover:bg-white/18 hover:border-white/30 hover:shadow-md active:scale-[0.98]"
          onClick={handleCreate}
          type="button"
        >
          <Plus className="size-3.5" />
          <span>新建对话</span>
        </button>

        <div className="flex items-center justify-between gap-1.5 pt-0.5">
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

      {/* 底部归档折叠抽屉 */}
      <div className="border-t border-line-hairline bg-surface-panel/30 p-2">
        <button
          className="flex w-full items-center justify-between rounded-xl px-2 py-1.5 text-[11px] text-muted transition hover:bg-white/8 hover:text-strong"
          onClick={() => $archiveOpen.set(!archiveOpen)}
          type="button"
        >
          <span className="flex items-center gap-1.5">
            <Archive className="size-3" />
            已归档会话{archivedSessions.length > 0 && ` (${archivedSessions.length})`}
          </span>
          <ChevronDown className={cn('size-3 transition-transform duration-200', archiveOpen && 'rotate-180')} />
        </button>
        {archiveOpen && (
          <div className="mt-1 max-h-40 space-y-0.5 overflow-y-auto pr-0.5">
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

  return (
    <div
      className={cn(
        'group flex cursor-pointer items-center gap-2 rounded-xl border px-2.5 py-1.5 transition-all duration-150 select-none',
        isActive
          ? 'border-white/25 bg-white/15 text-strong font-semibold shadow-sm backdrop-blur-md'
          : 'border-transparent text-muted hover:border-white/10 hover:bg-white/6 hover:text-strong'
      )}
      onClick={() => {
        if (!editing) {
          void onSwitch(session.id)
        }
      }}
    >
      {CustomIcon ? (
        <CustomIcon className={cn('size-3.5 shrink-0', isActive ? 'text-accent' : 'opacity-75')} />
      ) : isSpecial ? (
        <span className="shrink-0" title={presetName ?? ''}>
          <PresetIconBadge iconKey={session.system_preset_icon_key} />
        </span>
      ) : (
        <MessageCircle className="size-3.5 shrink-0 text-faint" />
      )}

      <div className="min-w-0 flex-1">
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
          <p className="truncate text-xs">
            {customLabel ?? session.title ?? (isSpecial ? (presetName ?? '专业会话') : '新对话')}
            {session.pinned && <Pin className="ml-1 inline size-2.5 text-accent opacity-75" />}
          </p>
        )}
        {session.preview && !editing && <p className="truncate text-[10px] text-faint opacity-80">{session.preview}</p>}
      </div>

      {badge && <span className="shrink-0 rounded-md bg-white/10 px-1 py-0.2 text-[9px] text-muted">{badge}</span>}

      <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
        {canRename && !editing && (
          <RowAction
            icon={Pencil}
            label={strings.chat.sessionRename.action}
            onClick={e => stopThen(e, () => setEditing(true))}
          />
        )}
        {actions}
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
