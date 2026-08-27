import type { ReactNode } from 'react'

import type { IconComponent } from '@/shared/lib/icons'
import { Loader2, X } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'

import { BTN_ICON, HINT_TEXT, NAV_ITEM, NAV_ITEM_ACTIVE } from './palette'

// 拖拽柄事件组的透传形状（usePanelDrag 的 bind）——shared 侧不依赖 companion hooks，
// 只要求它是可展开到 DOM 上的对象。
type DragBindProps = object

export interface NavItemDescriptor {
  id: string
  label: string
  icon: IconComponent
}

export function SettingsNav({
  items,
  activeId,
  onSelect
}: {
  items: readonly NavItemDescriptor[]
  activeId: string
  onSelect: (id: string) => void
}): React.JSX.Element {
  return (
    <nav className="flex flex-col gap-0.5">
      {items.map(item => (
        <button
          className={cn(activeId === item.id ? NAV_ITEM_ACTIVE : NAV_ITEM)}
          key={item.id}
          onClick={() => onSelect(item.id)}
          type="button"
        >
          <item.icon className="shrink-0 size-4 text-white/40" />
          <span className="min-w-0 flex-1 truncate">{item.label}</span>
        </button>
      ))}
    </nav>
  )
}

// 设置页骨架：粘性标题 + 独立滚动正文。
export function SettingsPage({
  title,
  hint,
  children
}: {
  title: string
  hint?: string
  children: ReactNode
}): React.JSX.Element {
  return (
    <section className="flex min-h-0 flex-1 flex-col">
      <header className="px-5 pb-3 pt-4">
        <h2 className="text-sm font-semibold text-white">{title}</h2>
        {hint && <p className={cn('mt-1', HINT_TEXT)}>{hint}</p>}
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-5">{children}</div>
    </section>
  )
}

// 卡片行容器：多行 SettingRow 之间用 hairline 分隔。
export function SettingCard({ children }: { children: ReactNode }): React.JSX.Element {
  return (
    <div className="divide-y divide-white/5 overflow-hidden rounded-xl border border-white/8 bg-[#1c1c21]">
      {children}
    </div>
  )
}

export function SettingRow({
  label,
  description,
  children,
  stacked = false
}: {
  label: ReactNode
  description?: ReactNode
  children: ReactNode
  // 控件较宽（分段控件 / 档位卡）时改为上下堆叠
  stacked?: boolean
}): React.JSX.Element {
  return (
    <div
      className={cn(
        'grid gap-2 px-3.5 py-3',
        stacked ? 'grid-cols-1' : 'sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center'
      )}
    >
      <div className="min-w-0">
        <div className="text-xs font-medium text-white/90">{label}</div>
        {description && <div className="mt-0.5 text-[11px] leading-relaxed text-white/40">{description}</div>}
      </div>
      <div className={cn('min-w-0', !stacked && 'sm:justify-self-end')}>{children}</div>
    </div>
  )
}

export function Toggle({
  checked,
  onChange,
  disabled = false,
  ariaLabel
}: {
  checked: boolean
  onChange: (next: boolean) => void
  disabled?: boolean
  ariaLabel?: string
}): React.JSX.Element {
  return (
    <button
      aria-checked={checked}
      aria-label={ariaLabel}
      className={cn(
        'relative h-5 w-9 shrink-0 rounded-full border transition-colors disabled:pointer-events-none disabled:opacity-40',
        checked ? 'border-transparent bg-[#6c8aff]' : 'border-white/15 bg-white/10'
      )}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      role="switch"
      type="button"
    >
      <span
        className={cn(
          'absolute top-1/2 size-4 -translate-y-1/2 rounded-full bg-white transition-[left] duration-150',
          checked ? 'left-[calc(100%-1.125rem)]' : 'left-0.5'
        )}
      />
    </button>
  )
}

export function Segmented<T extends string>({
  options,
  value,
  onChange
}: {
  options: ReadonlyArray<{ value: T; label: string }>
  value: T
  onChange: (next: T) => void
}): React.JSX.Element {
  return (
    <div className="flex gap-0.5 rounded-xl border border-white/10 bg-black/30 p-0.5">
      {options.map(o => (
        <button
          className={cn(
            'flex-1 rounded-[0.625rem] px-3 py-1.5 text-xs whitespace-nowrap transition',
            value === o.value ? 'bg-white/12 font-medium text-white' : 'text-white/55 hover:text-white'
          )}
          key={o.value}
          onClick={() => onChange(o.value)}
          type="button"
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function PanelHeader({
  title,
  icon: Icon,
  onClose,
  dragBind,
  closeLabel = '关闭'
}: {
  title: ReactNode
  icon?: IconComponent
  onClose: () => void
  // 伙伴窗浮层把拖拽柄事件铺在头部；工具窗不传。
  dragBind?: DragBindProps
  closeLabel?: string
}): React.JSX.Element {
  return (
    <div
      className={cn(
        'flex items-center justify-between gap-2 border-b border-white/10 px-4 py-2.5',
        dragBind && 'cursor-grab active:cursor-grabbing'
      )}
      title={dragBind ? '拖动以移动面板' : undefined}
      {...dragBind}
    >
      <div className="flex items-center gap-2">
        {Icon && <Icon className="size-4 text-white/50" />}
        <h2 className="text-sm font-semibold text-white">{title}</h2>
      </div>
      <button aria-label={closeLabel} className={BTN_ICON} onClick={onClose} type="button">
        <X />
      </button>
    </div>
  )
}

export function Spinner({ className }: { className?: string }): React.JSX.Element {
  return <Loader2 className={cn('size-4 animate-spin text-white/40', className)} />
}

export function EmptyState({
  title,
  description,
  action
}: {
  title: string
  description?: string
  action?: ReactNode
}): React.JSX.Element {
  return (
    <div className="grid min-h-36 place-items-center px-4 py-8 text-center">
      <div className="max-w-sm">
        <div className="text-xs font-medium text-white/70">{title}</div>
        {description && <div className="mt-1 text-[11px] leading-relaxed text-white/40">{description}</div>}
        {action && <div className="mt-3 flex justify-center">{action}</div>}
      </div>
    </div>
  )
}
