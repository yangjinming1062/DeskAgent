import { useMemo } from 'react'

import { useI18n } from '@/i18n'
import type { Translations } from '@/i18n/types'
import { Loader2, Users } from '@/lib/icons'
import { cn } from '@/lib/utils'
import type { SubagentProgress } from '@/store/subagents'

interface SubagentsPopoverProps {
  items: readonly SubagentProgress[]
  onOpen: (sessionId: string) => void
}

const STATUS_DOT: Record<SubagentProgress['status'], string> = {
  running: 'bg-(--chrome-action-active) animate-pulse',
  queued: 'bg-muted-foreground',
  completed: 'bg-emerald-500',
  failed: 'bg-destructive',
  interrupted: 'bg-muted-foreground'
}

type StatusLabelKey = 'done' | 'failed' | 'running' | 'streaming'

const STATUS_LABEL: Record<SubagentProgress['status'], StatusLabelKey> = {
  running: 'running',
  failed: 'failed',
  queued: 'streaming',
  completed: 'done',
  interrupted: 'done'
}

export function SubagentsPopover({ items, onOpen }: SubagentsPopoverProps) {
  const { t } = useI18n()
  const copy = t.agents

  const ordered = useMemo(
    () =>
      [...items].sort((a, b) => {
        if (a.status === 'running' && b.status !== 'running') {
          return -1
        }

        if (b.status === 'running' && a.status !== 'running') {
          return 1
        }

        return b.startedAt - a.startedAt
      }),
    [items]
  )

  return (
    <div className="text-sm">
      <div className="flex items-center gap-2 px-3 py-2.5">
        <Users className="size-3.5 text-primary" />
        <span className="font-medium">{copy.title}</span>
        <span className="text-xs text-muted-foreground">{copy.subtitle}</span>
      </div>

      {ordered.length === 0 ? (
        <div className="border-t border-border/50 px-3 py-3 text-xs text-muted-foreground">
          <div className="font-medium text-foreground">{copy.emptyTitle}</div>
          <div className="mt-1">{copy.emptyDesc}</div>
        </div>
      ) : (
        <ul className="max-h-80 overflow-y-auto border-t border-border/50 py-1">
          {ordered.map(item => (
            <SubagentRow copy={copy} item={item} key={item.id} onOpen={onOpen} />
          ))}
        </ul>
      )}
    </div>
  )
}

interface SubagentRowProps {
  copy: Translations['agents']
  item: SubagentProgress
  onOpen: (sessionId: string) => void
}

function SubagentRow({ copy, item, onOpen }: SubagentRowProps) {
  const sessionId = item.sessionId
  const isClickable = typeof sessionId === 'string' && sessionId.length > 0
  const isRunning = item.status === 'running'

  return (
    <li>
      <button
        className={cn(
          'flex w-full items-start gap-2 px-3 py-2 text-left text-xs transition-colors',
          isClickable
            ? 'hover:bg-(--chrome-action-hover) focus-visible:bg-(--chrome-action-hover) focus-visible:outline-none'
            : 'cursor-default opacity-60'
        )}
        disabled={!isClickable}
        onClick={() => onOpen(sessionId as string)}
        type="button"
      >
        <span className={cn('mt-1 size-1.5 shrink-0 rounded-full', STATUS_DOT[item.status])} />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            {isRunning && <Loader2 className="size-3 shrink-0 animate-spin text-(--ui-text-secondary)" />}
            <span className="truncate text-foreground">{item.goal || 'Subagent'}</span>
          </span>
          <span className="mt-0.5 block text-[0.6875rem] text-muted-foreground">
            {copy[STATUS_LABEL[item.status]] as string}
          </span>
        </span>
      </button>
    </li>
  )
}
