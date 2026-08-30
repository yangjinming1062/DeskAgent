import type { ReactNode } from 'react'

import { PAGE_INSET_X } from '@/shared'
import { cn } from '@/shared/lib/utils'
import { CHIP, CHIP_ACTIVE, LoadingBlock } from '@/shared/panel'

// 应用设置页基元——视觉词汇与 shared/panel 同源（石墨表面阶梯 + hairline）。
// 容器背景由 hub/overlays 提供，此处只约束正文宽度并提供滚动 + 内边距。

export function SettingsContent({ children }: { children: ReactNode }): React.JSX.Element {
  return (
    <section className="min-h-0 overflow-hidden">
      <div className={cn('h-full min-h-0 overflow-y-auto pb-20', PAGE_INSET_X)}>
        <div className="mx-auto w-full max-w-4xl text-strong">{children}</div>
      </div>
    </section>
  )
}

export function Pill({
  tone = 'muted',
  children
}: {
  tone?: 'muted' | 'primary'
  children: ReactNode
}): React.JSX.Element {
  return <span className={tone === 'primary' ? CHIP_ACTIVE : CHIP}>{children}</span>
}

export function SectionHeading({ title, meta }: { title: string; meta?: string }): React.JSX.Element {
  return (
    <div className="mb-2 flex items-center gap-2 text-xs font-semibold tracking-wide text-strong">
      <span className="size-1.5 rounded-full bg-accent shadow-[0_0_4px_var(--ui-accent)]" />
      <span>{title}</span>
      {meta && <Pill>{meta}</Pill>}
    </div>
  )
}

export function SettingsSubsection({
  children,
  intro,
  title
}: {
  children: ReactNode
  intro?: string
  title: string
}): React.JSX.Element {
  return (
    <div className="space-y-2.5">
      <div className="flex items-center gap-1.5 text-xs font-semibold text-strong">
        <span>{title}</span>
      </div>
      {intro ? <p className="text-[10px] text-faint">{intro}</p> : null}
      <div className="space-y-2">{children}</div>
    </div>
  )
}

export function ListRow({
  title,
  description,
  action,
  below,
  wide = false
}: {
  title: ReactNode
  description?: ReactNode
  action?: ReactNode
  below?: ReactNode
  wide?: boolean
}): React.JSX.Element {
  return (
    <div
      className={cn(
        'group relative grid gap-3 rounded-xl border border-line-hairline bg-fill-faint p-3 transition-all duration-150 hover:border-line-strong hover:bg-fill-hover sm:grid-cols-[minmax(0,1fr)_minmax(15rem,22rem)] sm:items-center',
        wide && 'sm:grid-cols-1 sm:items-start'
      )}
    >
      <div className="min-w-0">
        <div className="text-xs font-medium text-strong">{title}</div>
        {description && <div className="mt-1 text-[11px] leading-relaxed text-muted">{description}</div>}
        {below}
      </div>
      {action && <div className={cn('min-w-0', !wide && 'sm:justify-self-end')}>{action}</div>}
    </div>
  )
}

export function LoadingState({ label }: { label: string }): React.JSX.Element {
  return <LoadingBlock label={label} />
}

export function EmptyState({ title, description }: { title: string; description: string }): React.JSX.Element {
  return (
    <div className="grid min-h-48 place-items-center text-center">
      <div>
        <div className="text-sm font-medium text-strong">{title}</div>
        <div className="mt-1 text-xs text-faint">{description}</div>
      </div>
    </div>
  )
}