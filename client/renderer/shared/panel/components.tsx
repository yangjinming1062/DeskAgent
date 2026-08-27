import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import type { IconComponent } from '@/shared/lib/icons'
import { ChevronDown, Eye, EyeOff, Loader2, Search, X } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'

import {
  BTN_DANGER,
  BTN_ICON,
  BTN_PRIMARY,
  BTN_SUBTLE,
  HINT_TEXT,
  INPUT_CLASS,
  NAV_ITEM,
  NAV_ITEM_ACTIVE
} from './palette'

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

export function PanelSelect<T extends string>({
  value,
  options,
  onChange,
  disabled = false,
  widthClass = 'w-36',
  ariaLabel
}: {
  value: T
  options: ReadonlyArray<{ value: T; label: string }>
  onChange: (next: T) => void
  disabled?: boolean
  widthClass?: string
  ariaLabel?: string
}): React.JSX.Element {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) {
      return
    }

    const onPointerDown = (e: PointerEvent): void => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }

    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        setOpen(false)
      }
    }

    document.addEventListener('pointerdown', onPointerDown, true)
    window.addEventListener('keydown', onKey, true)

    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true)
      window.removeEventListener('keydown', onKey, true)
    }
  }, [open])

  const selected = options.find(o => o.value === value)

  return (
    <div className={cn('relative', widthClass)} ref={rootRef}>
      <button
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-label={ariaLabel}
        className="flex h-8 w-full items-center justify-between gap-2 rounded-lg border border-white/12 bg-white/5 px-3 text-xs text-white transition hover:bg-white/10 disabled:pointer-events-none disabled:opacity-40"
        disabled={disabled}
        onClick={() => setOpen(o => !o)}
        type="button"
      >
        <span className="truncate">{selected?.label ?? value}</span>
        <ChevronDown className={cn('size-3.5 shrink-0 text-white/40 transition-transform', open && 'rotate-180')} />
      </button>
      {open && (
        <div className="absolute right-0 z-50 mt-1 min-w-full overflow-hidden rounded-xl border border-white/12 bg-[#141416] p-1 shadow-2xl">
          {options.map(o => (
            <button
              aria-selected={o.value === value}
              className={cn(
                'flex h-7 w-full items-center rounded-lg px-2.5 text-left text-xs transition',
                o.value === value ? 'bg-[#6c8aff]/15 font-medium text-white' : 'text-white/70 hover:bg-white/5'
              )}
              key={o.value}
              onClick={() => {
                onChange(o.value)
                setOpen(false)
              }}
              role="option"
              type="button"
            >
              {o.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// 密钥输入三件套：密码输入 + 显隐切换 + 清除（已存档时）。
export function SecretInput({
  value,
  onChange,
  isSet,
  onClear,
  placeholder,
  disabled = false,
  revealLabel = '显示密钥',
  hideLabel = '隐藏密钥',
  clearLabel = '清除密钥'
}: {
  value: string
  onChange: (next: string) => void
  isSet: boolean
  onClear?: () => void
  placeholder?: string
  disabled?: boolean
  revealLabel?: string
  hideLabel?: string
  clearLabel?: string
}): React.JSX.Element {
  const [revealed, setRevealed] = useState(false)

  return (
    <div className="flex items-center gap-1">
      <input
        className={cn(INPUT_CLASS, 'max-w-xs')}
        disabled={disabled}
        onChange={e => onChange(e.currentTarget.value)}
        placeholder={placeholder}
        type={revealed ? 'text' : 'password'}
        value={value}
      />
      <button
        aria-label={revealed ? hideLabel : revealLabel}
        className={BTN_ICON}
        disabled={disabled}
        onClick={() => setRevealed(prev => !prev)}
        type="button"
      >
        {revealed ? <EyeOff /> : <Eye />}
      </button>
      {isSet && onClear && (
        <button aria-label={clearLabel} className={BTN_ICON} disabled={disabled} onClick={onClear} type="button">
          <X />
        </button>
      )}
    </div>
  )
}

export function SearchField({
  value,
  onChange,
  placeholder,
  ariaLabel
}: {
  value: string
  onChange: (next: string) => void
  placeholder: string
  ariaLabel?: string
}): React.JSX.Element {
  return (
    <div className="relative w-full max-w-sm">
      <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-white/35" />
      <input
        aria-label={ariaLabel}
        className={cn(INPUT_CLASS, 'py-1.5 pl-8 pr-8')}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        type="text"
        value={value}
      />
      {value && (
        <button
          aria-label="清空搜索"
          className="absolute right-1.5 top-1/2 flex size-6 -translate-y-1/2 items-center justify-center rounded-md text-white/40 transition hover:bg-white/10 hover:text-white"
          onClick={() => onChange('')}
          type="button"
        >
          <X className="size-3.5" />
        </button>
      )}
    </div>
  )
}

export function LoadingBlock({ label }: { label?: string }): React.JSX.Element {
  return (
    <div className="flex items-center justify-center gap-2 py-12 text-xs text-white/40">
      <Spinner />
      {label}
    </div>
  )
}

interface ConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  confirmLabel: string
  cancelLabel?: string
  variant?: 'default' | 'destructive'
  onConfirm: () => void | Promise<void>
}

// 警示性确认的小型弹窗（清空密钥、重置配置）。自包含深色卡，不依赖 Radix。
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  cancelLabel = '取消',
  variant = 'default',
  onConfirm
}: ConfirmDialogProps): React.JSX.Element {
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) {
      return
    }

    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape' && !busy) {
        e.preventDefault()
        e.stopPropagation()
        onOpenChange(false)
      }
    }

    window.addEventListener('keydown', onKey, true)

    return () => window.removeEventListener('keydown', onKey, true)
  }, [open, busy, onOpenChange])

  if (!open) {
    return <></>
  }

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/40 p-6 backdrop-blur-sm"
      onPointerDown={e => {
        if (!busy && e.target === e.currentTarget) {
          onOpenChange(false)
        }
      }}
    >
      <div className="w-full max-w-md rounded-2xl border border-white/12 bg-[#141416] p-5 text-white shadow-2xl">
        <h3 className="text-sm font-semibold">{title}</h3>
        {description && <p className="mt-2 text-xs leading-relaxed text-white/60">{description}</p>}
        <div className="mt-5 flex justify-end gap-2">
          <button className={BTN_SUBTLE} disabled={busy} onClick={() => onOpenChange(false)} type="button">
            {cancelLabel}
          </button>
          <button
            className={variant === 'destructive' ? BTN_DANGER : BTN_PRIMARY}
            disabled={busy}
            onClick={() => {
              setBusy(true)

              void Promise.resolve(onConfirm())
                .then(() => onOpenChange(false))
                .catch(() => {
                  // 出错保留弹窗供重试；错误提示由调用方 notify 负责。
                })
                .finally(() => setBusy(false))
            }}
            type="button"
          >
            {busy ? '处理中…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
