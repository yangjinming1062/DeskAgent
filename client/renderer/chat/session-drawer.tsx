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
  type IconComponent,
  MessageCircle,
  Messages,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Trash2
} from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { BTN_PRIMARY, INPUT_CLASS, SearchField } from '@/shared/panel'
import { strings } from '@/shared/strings'
import type { SessionInfo } from '@/shared/types/spiritagent'

const SORT_OPTIONS: { icon: IconComponent; label: string; value: SessionSort }[] = [
  { icon: Clock, label: '按最近活跃排序', value: 'recent' },
  { icon: CalendarPlus, label: '按创建时间排序', value: 'created' },
  { icon: Messages, label: '按消息数排序', value: 'messages' }
]

// 聊天窗内左侧会话抽屉：搜索 / 排序 / 置顶 / 归档 / 切换 / 新建 / 删除历史对话（原居中弹层的抽屉化形态）。
// 选中或新建后由 ChatDock 关闭抽屉；置顶/归档/排序即时生效不关抽屉。
export function SessionDrawer({ onClose }: { onClose: () => void }): React.JSX.Element {
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
    onClose()
  }

  const handleSwitch = async (id: string): Promise<void> => {
    await switchSession(id)
    onClose()
  }

  const isSpecialSession = (s: SessionInfo): boolean => s.kind === 'special'

  // 服务端排序保证系统预设 + 手动置顶项是结果前缀，这里按谓词分组即可，不重排。
  const pinnedSessions = sessions.filter(s => isSpecialSession(s) || s.pinned)
  const regularSessions = sessions.filter(s => !isSpecialSession(s) && !s.pinned)

  return (
    <aside className="sa-drawer-in flex w-64 shrink-0 flex-col border-r border-line-standard bg-surface-chrome">
      <div className="flex items-center justify-between gap-2 px-3 pb-2 pt-3">
        <h3 className="text-xs font-semibold text-body">对话</h3>
        <div className="flex items-center gap-1">
          {SORT_OPTIONS.map(({ icon: Icon, label, value }) => (
            <button
              aria-label={label}
              className={cn(
                'rounded-md p-1 transition',
                value === sort ? 'bg-accent-soft text-accent' : 'text-faint hover:bg-fill-hover hover:text-strong'
              )}
              key={value}
              onClick={() => setSessionSort(value)}
              title={label}
              type="button"
            >
              <Icon className="size-3.5" />
            </button>
          ))}
          <button
            className={cn(BTN_PRIMARY, 'h-6 gap-1 px-2 text-[11px]')}
            onClick={() => void handleCreate()}
            type="button"
          >
            <Plus className="size-3" />
            新建
          </button>
        </div>
      </div>

      <div className="px-3 pb-2">
        <SearchField ariaLabel="搜索对话" onChange={$sessionSearch.set} placeholder="搜索对话…" value={search} />
      </div>

      <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
        {searchActive ? (
          searchLoading ? (
            <EmptyHint text="搜索中…" />
          ) : searchResults.length === 0 ? (
            <EmptyHint text="没有匹配的对话" />
          ) : (
            searchResults.map(s => (
              <SessionRow
                badge={s.archived ? '已归档' : undefined}
                isActive={s.id === activeSessionId}
                key={s.id}
                onSwitch={handleSwitch}
                session={s}
              />
            ))
          )
        ) : loading ? (
          <EmptyHint text="加载中…" />
        ) : sessions.length === 0 ? (
          <EmptyHint text="暂无对话记录" />
        ) : (
          <>
            {pinnedSessions.map(s => (
              <SessionRow
                actions={
                  isSpecialSession(s) ? undefined : (
                    <>
                      <RowAction
                        icon={PinOff}
                        label="取消置顶"
                        onClick={e => stopThen(e, () => void pinSession(s.id, false))}
                      />
                      <RowAction
                        icon={Archive}
                        label="归档对话"
                        onClick={e => stopThen(e, () => void archiveSession(s.id, true))}
                      />
                    </>
                  )
                }
                isActive={s.id === activeSessionId}
                key={s.id}
                onSwitch={handleSwitch}
                session={s}
              />
            ))}
            {pinnedSessions.length > 0 && regularSessions.length > 0 && (
              <div className="my-1.5 border-t border-line-standard" />
            )}
            {regularSessions.map(s => (
              <SessionRow
                actions={
                  <>
                    <RowAction
                      icon={Pin}
                      label="置顶对话"
                      onClick={e => stopThen(e, () => void pinSession(s.id, true))}
                    />
                    <RowAction
                      icon={Archive}
                      label="归档对话"
                      onClick={e => stopThen(e, () => void archiveSession(s.id, true))}
                    />
                    <RowAction
                      danger
                      icon={Trash2}
                      label="删除对话"
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
          </>
        )}
      </div>

      <div className="border-t border-line-standard px-2 py-1.5">
        <button
          className="flex w-full items-center justify-between rounded-md px-1.5 py-1 text-[11px] text-muted transition hover:bg-fill-faint hover:text-body"
          onClick={() => $archiveOpen.set(!archiveOpen)}
          type="button"
        >
          <span className="flex items-center gap-1.5">
            <Archive className="size-3" />
            已归档{archivedSessions.length > 0 && ` (${archivedSessions.length})`}
          </span>
          <ChevronDown className={cn('size-3 transition-transform', archiveOpen && 'rotate-180')} />
        </button>
        {archiveOpen && (
          <div className="mt-1 max-h-48 space-y-0.5 overflow-y-auto">
            {archivedLoading ? (
              <EmptyHint text="加载中…" />
            ) : archivedSessions.length === 0 ? (
              <EmptyHint text="暂无归档对话" />
            ) : (
              archivedSessions.map(s => (
                <SessionRow
                  actions={
                    <>
                      <RowAction
                        icon={ArchiveOff}
                        label="取消归档"
                        onClick={e => stopThen(e, () => void archiveSession(s.id, false))}
                      />
                      <RowAction
                        danger
                        icon={Trash2}
                        label="删除对话"
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
          presets={presets}
        />
      )}
    </aside>
  )
}

function SessionRow({
  session,
  isActive,
  actions,
  badge,
  onSwitch
}: {
  session: SessionInfo
  isActive: boolean
  actions?: React.ReactNode
  badge?: string
  onSwitch: (id: string) => Promise<void> | void
}): React.JSX.Element {
  const isSpecial = session.kind === 'special'
  const canRename = !isSpecial && session.kind !== 'im'
  const [editing, setEditing] = useState(false)
  const presets = useStore($systemPresets)

  const presetName = session.system_preset_id ? presets.find(p => p.id === session.system_preset_id)?.name : undefined

  return (
    <div
      className={`group flex cursor-pointer items-center gap-2 rounded-lg border px-2.5 py-2 transition ${
        isActive
          ? 'border-accent-line bg-accent-soft'
          : 'border-transparent hover:border-line-standard hover:bg-fill-faint'
      }`}
      onClick={() => {
        if (!editing) {
          void onSwitch(session.id)
        }
      }}
    >
      {isSpecial ? (
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
          <p className={cn('truncate text-xs', isActive ? 'font-medium text-strong' : 'text-strong')}>
            {session.title || (isSpecial ? (presetName ?? '系统对话') : '新建对话')}
            {session.pinned && <Pin className="ml-1 inline size-3 text-faint" />}
          </p>
        )}
        {session.preview && <p className="mt-0.5 truncate text-[10px] text-faint">{session.preview}</p>}
      </div>
      {!isSpecial && session.system_preset_icon_key && (
        <span className="shrink-0" title={presetName ?? ''}>
          <PresetIconBadge iconKey={session.system_preset_icon_key} />
        </span>
      )}
      {badge && <span className="shrink-0 rounded bg-fill-hover px-1 py-0.5 text-[9px] text-muted">{badge}</span>}
      <div className="flex shrink-0 items-center">
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
        'rounded-md p-1 text-faint opacity-0 transition group-hover:opacity-100',
        danger ? 'hover:bg-rose-500/15 hover:text-rose-300' : 'hover:bg-fill-hover hover:text-strong'
      )}
      onClick={onClick}
      title={label}
      type="button"
    >
      <Icon className="size-3.5" />
    </button>
  )
}

function EmptyHint({ text }: { text: string }): React.JSX.Element {
  return <div className="py-6 text-center text-xs text-faint">{text}</div>
}

function stopThen(e: React.MouseEvent, fn: () => void): void {
  e.stopPropagation()
  fn()
}
