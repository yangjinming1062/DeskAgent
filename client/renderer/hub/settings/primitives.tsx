import type { ReactNode } from 'react'

import { PAGE_INSET_X } from '@/shared'
import { PageLoader } from '@/shared/components/page-loader'
import { cn } from '@/shared/lib/utils'

// 应用设置深色玻璃风面板——对齐伙伴设置 (companion/settings-overlay.tsx) 的色板。
// 容器由 hub/overlays 提供 bg-black/60 backdrop-blur-md，
// 此处只约束正文宽度并提供滚动 + 内边距。

export function SettingsContent({ children }: { children: ReactNode }): React.JSX.Element {
  return (
    <section className="min-h-0 overflow-hidden">
      <div className={cn('h-full min-h-0 overflow-y-auto pb-20', PAGE_INSET_X)}>
        <div className="mx-auto w-full max-w-4xl text-white">{children}</div>
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
  return (
    <span
      className={cn(
        'rounded-full border px-2 py-0.5 text-[10px]',
        tone === 'primary' ? 'border-white/25 bg-white/15 text-white' : 'border-white/15 bg-white/10 text-white/70'
      )}
    >
      {children}
    </span>
  )
}

export function FilterPill({
  active,
  children,
  onClick
}: {
  active: boolean
  children: ReactNode
  onClick: () => void
}): React.JSX.Element {
  return (
    <button
      className={cn(
        'rounded-full px-2.5 py-0.5 text-[10px] transition',
        active ? 'bg-white/20 font-medium text-white' : 'bg-white/5 text-white/50 hover:bg-white/10'
      )}
      onClick={onClick}
      type="button"
    >
      {children}
    </button>
  )
}

export function SectionHeading({ title, meta }: { title: string; meta?: string }): React.JSX.Element {
  return (
    <div className="mb-1.5 flex items-center gap-2 text-xs font-medium text-white/80">
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
      <div className="text-xs font-medium text-white/80">{title}</div>
      {intro ? <p className="text-[10px] text-white/40">{intro}</p> : null}
      <div className="space-y-2">{children}</div>
    </div>
  )
}

export function ListRow({
  title,
  description,
  hint,
  action,
  below,
  wide = false
}: {
  title: ReactNode
  description?: ReactNode
  hint?: ReactNode
  action?: ReactNode
  below?: ReactNode
  wide?: boolean
}): React.JSX.Element {
  return (
    <div
      className={cn(
        'grid gap-3 rounded-lg px-3 py-2 transition hover:bg-white/5 sm:grid-cols-[minmax(0,1fr)_minmax(15rem,22rem)] sm:items-center',
        wide && 'sm:grid-cols-1 sm:items-start'
      )}
    >
      <div className="min-w-0">
        <div className="text-xs font-medium text-white/90">{title}</div>
        {description && <div className="mt-1 text-[10px] leading-relaxed text-white/40">{description}</div>}
        {hint && <div className="mt-1 block font-mono text-[0.68rem] text-white/30">{hint}</div>}
        {below}
      </div>
      {action && <div className={cn('min-w-0', !wide && 'sm:justify-self-end')}>{action}</div>}
    </div>
  )
}

export function LoadingState({ label }: { label: string }): React.JSX.Element {
  return <PageLoader label={label} />
}

export function EmptyState({ title, description }: { title: string; description: string }): React.JSX.Element {
  return (
    <div className="grid min-h-48 place-items-center text-center">
      <div>
        <div className="text-sm font-medium text-white/80">{title}</div>
        <div className="mt-1 text-xs text-white/40">{description}</div>
      </div>
    </div>
  )
}
