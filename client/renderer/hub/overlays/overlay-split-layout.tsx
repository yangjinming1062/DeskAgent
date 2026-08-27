import type { ReactNode } from 'react'

import { PAGE_INSET_X } from '@/shared'
import type { IconComponent } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { NAV_ITEM_ACTIVE, SURFACE_CHROME } from '@/shared/panel'

interface OverlaySplitLayoutProps {
  children: ReactNode
  className?: string
}

interface OverlaySidebarProps {
  children: ReactNode
  className?: string
}

interface OverlayMainProps {
  children: ReactNode
  className?: string
}

interface OverlayNavItemProps {
  active: boolean
  icon: IconComponent
  label: string
  // 作为另一个导航项的缩进子项渲染：图标更小、激活态更弱，
  // 不与带框的父项争抢视觉。
  nested?: boolean
  onClick: () => void
  trailing?: ReactNode
}

export function OverlaySplitLayout({ children, className }: OverlaySplitLayoutProps): React.JSX.Element {
  return (
    <div
      className={cn(
        'grid h-full min-h-0 flex-1 grid-cols-[13rem_minmax(0,1fr)] overflow-hidden bg-transparent max-[47.5rem]:grid-cols-1',
        className
      )}
    >
      {children}
    </div>
  )
}

export function OverlaySidebar({ children, className }: OverlaySidebarProps): React.JSX.Element {
  return (
    <aside
      className={cn(
        // 侧栏石墨实体（与伙伴设置同一表面阶梯），仅用右侧细线分隔。
        SURFACE_CHROME,
        'flex min-h-0 flex-col gap-0.5 overflow-y-auto border-r border-white/10 px-2.5 pb-3 pt-[calc(var(--titlebar-height)+1rem)]',
        className
      )}
    >
      {children}
    </aside>
  )
}

export function OverlayMain({ children, className }: OverlayMainProps): React.JSX.Element {
  return (
    <main
      className={cn(
        'flex min-h-0 flex-1 flex-col overflow-hidden bg-transparent pb-3 pt-[calc(var(--titlebar-height)+1rem)]',
        PAGE_INSET_X,
        className
      )}
    >
      {children}
    </main>
  )
}

export function OverlayNavItem({
  active,
  icon: Icon,
  label,
  nested,
  onClick,
  trailing
}: OverlayNavItemProps): React.JSX.Element {
  return (
    <button
      className={cn(
        'flex h-7 w-full items-center justify-start gap-2 rounded-md border px-2 text-left text-xs font-normal transition-colors',
        nested
          ? active
            ? 'border-transparent bg-white/10 font-medium text-white'
            : 'border-transparent bg-transparent text-white/50 hover:bg-white/5 hover:text-white'
          : active
            ? NAV_ITEM_ACTIVE
            : 'border-transparent bg-transparent text-white/60 hover:bg-white/5 hover:text-white'
      )}
      onClick={onClick}
      type="button"
    >
      <Icon className={cn('shrink-0', nested ? 'size-3.5' : 'size-4', active ? 'text-white/80' : 'text-white/40')} />
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {trailing}
    </button>
  )
}
