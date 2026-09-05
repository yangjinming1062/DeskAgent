import { useStore } from '@nanostores/react'
import type React from 'react'

import { triggerHaptic } from '@/shared/lib/haptics'
import { ArrowBackUp, Eye, Home, RefreshCw, Sparkles } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { SettingCard, SettingsContent } from '@/shared/panel'
import { notify } from '@/shared/store/notifications'

import {
  $backdropStatus,
  $roomHistory,
  $roomPolicy,
  regenerateRoom,
  rollbackRoom,
  setRoomPolicy
} from './room-backdrop-store'

// 不同于 shared/panel 的 SectionHeading——此处强调分组小标题（卡组上方），配色更弱。
function GroupHeading({ subtitle, title }: { subtitle?: string; title: string }): React.JSX.Element {
  return (
    <div className="flex items-baseline justify-between pb-1.5 pt-1">
      <h3 className="text-[11px] font-semibold uppercase tracking-wider text-faint">{title}</h3>
      {subtitle ? <span className="text-[10.5px] text-faint">{subtitle}</span> : null}
    </div>
  )
}

export function RoomPage(): React.JSX.Element {
  const history = useStore($roomHistory)
  const policy = useStore($roomPolicy)
  const status = useStore($backdropStatus)
  const busy = status === 'pending'

  const handleChange = async (): Promise<void> => {
    triggerHaptic('open')
    await regenerateRoom()
  }

  const handleRollback = async (backdropId: string): Promise<void> => {
    triggerHaptic('tap')
    await rollbackRoom(backdropId)
  }

  const handleToggleLock = async (): Promise<void> => {
    triggerHaptic('selection')
    const next = policy === 'locked' ? 'llm_may_replace' : 'locked'
    await setRoomPolicy(next)
    notify({
      kind: 'info',
      message: next === 'locked' ? '已锁定：不让角色自己换房' : '已解锁：角色可以自主换房'
    })
  }

  return (
    <SettingsContent>
      <p className="mb-6 text-[11px] leading-relaxed text-faint">
        换个心情、回滚到之前的房间、或者锁定房间政策——都在这里。
      </p>

      <SettingCard>
        <div className="flex items-center gap-3 px-3.5 py-3">
          <Home className="size-4 text-accent" />
          <span className="flex-1 text-xs text-strong">
            {status === 'pending' ? '正在收拾新房间…' : status === 'failed' ? '房间还在收拾' : '房间已就位'}
          </span>
        </div>

        <button
          className={cn(
            'flex w-full items-center justify-between gap-3 px-3.5 py-3 text-left transition',
            busy ? 'opacity-50 pointer-events-none' : 'hover:bg-fill-hover'
          )}
          disabled={busy}
          onClick={() => void handleChange()}
          type="button"
        >
          <span className="flex items-center gap-2">
            <Sparkles className="size-3.5 text-accent" />
            <span className="text-xs text-strong">换一间房间</span>
          </span>
          <span className="text-[11px] text-faint">生成新的房间图</span>
        </button>
      </SettingCard>

      <GroupHeading subtitle="最近 5 张" title="回滚历史" />

      <SettingCard>
        {history.length === 0 ? (
          <p className="px-3.5 py-4 text-center text-[11px] text-faint">还没有可回滚的历史</p>
        ) : (
          <div className="grid grid-cols-5 gap-2 p-3.5">
            {history.map(entry => (
              <button
                aria-label={`回滚到房间 ${entry.id}`}
                className="group relative aspect-video overflow-hidden rounded-lg border border-line-standard transition hover:border-accent"
                key={entry.id}
                onClick={() => void handleRollback(entry.id)}
                type="button"
              >
                <img alt={entry.brief || '历史房间'} className="h-full w-full object-cover" src={entry.thumbnailUrl} />
                <span className="absolute inset-0 flex items-center justify-center bg-black/55 opacity-0 transition group-hover:opacity-100">
                  <ArrowBackUp className="size-3.5 text-on-accent" />
                </span>
              </button>
            ))}
          </div>
        )}
      </SettingCard>

      <GroupHeading title="房间政策" />

      <SettingCard>
        <button
          className={cn(
            'flex w-full items-center justify-between gap-3 px-3.5 py-3 text-left transition hover:bg-fill-hover'
          )}
          onClick={() => void handleToggleLock()}
          type="button"
        >
          <span className="flex items-center gap-2">
            <Eye className={cn('size-3.5', policy === 'locked' ? 'text-amber-300' : 'text-muted')} />
            <span className="text-xs text-strong">
              {policy === 'locked' ? '已锁定：拒绝角色自主换房' : '未锁定：角色可自主换房'}
            </span>
          </span>
          <span className="text-[11px] text-faint">点击切换</span>
        </button>
        <p className="px-3.5 pb-3 text-[10.5px] leading-relaxed text-faint">
          锁定后仍响应换装联动与你的显式请求；不锁定则角色可按档位自主决定换房时机。
        </p>
      </SettingCard>

      <button
        className="mt-3 inline-flex items-center gap-1.5 self-start text-[11px] text-faint transition hover:text-accent"
        onClick={() => void handleChange()}
        type="button"
      >
        <RefreshCw className="size-3" />
        重新生成当前房间
      </button>
    </SettingsContent>
  )
}
