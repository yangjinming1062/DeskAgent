import { useStore } from '@nanostores/react'
import { type ReactNode, type Ref, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import { triggerHaptic } from '@/shared/lib/haptics'
import { AlertCircle, AlertTriangle, Check, CheckCircle2, Copy, type IconComponent, Info, X } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import {
  $notifications,
  type AppNotification,
  clearNotifications,
  dismissNotification,
  type NotificationKind
} from '@/shared/store/notifications'
import { strings } from '@/shared/strings'

// toast 属于瞬时浮层——两个窗口共用同一套显式深色玻璃样式（不依赖 --dt token，
// 精灵窗的浅色种子不会把 toast 染白）。
const tone: Record<NotificationKind, { icon: IconComponent; iconClass: string }> = {
  error: { icon: AlertCircle, iconClass: 'text-rose-400' },
  warning: { icon: AlertTriangle, iconClass: 'text-amber-300' },
  info: { icon: Info, iconClass: 'text-white/50' },
  success: { icon: CheckCircle2, iconClass: 'text-emerald-400' }
}

const STACK_SURFACE =
  'pointer-events-auto rounded-xl border border-white/12 bg-black/65 text-white shadow-xl backdrop-blur-lg'

// regionRef 把 portal 容器的 DOM 引用交给调用方——精灵透明窗口需要借此把
// toast 矩形注册进交互区域登记处（shared 不得反向依赖 companion，所以经 props 透传）。
export function NotificationStack({ regionRef }: { regionRef?: Ref<HTMLDivElement> }): React.JSX.Element | null {
  const notifications = useStore($notifications)
  const t = strings
  const lastNotificationIdRef = useRef<string | null>(null)
  const [expanded, setExpanded] = useState(false)
  const copy = t.notifications

  useEffect(() => {
    if (notifications.length <= 1) {
      setExpanded(false)
    }
  }, [notifications.length])

  useEffect(() => {
    const latest = notifications[0]

    if (!latest || latest.id === lastNotificationIdRef.current) {
      return
    }

    lastNotificationIdRef.current = latest.id

    if (latest.kind === 'success') {
      triggerHaptic('success')
    } else if (latest.kind === 'error') {
      triggerHaptic('error')
    } else if (latest.kind === 'warning') {
      triggerHaptic('warning')
    }
  }, [notifications])

  if (notifications.length === 0) {
    return null
  }

  const [latest, ...olderNotifications] = notifications
  const overflowCount = olderNotifications.length

  // 渲染到 <body>，z-index 高于 Radix 对话框层（overlay z-[120]、content z-[130]）。
  // 不做 portal 时，堆叠上下文留在 React 根子树内，body 级对话框 / overlay 的 portal
  // 会盖在上面——所以在对话框打开时（或 OverlayView 页面上）触发的成功提示
  // 会不可见。titlebar-height 变量只在 app shell 作用域内存在，
  // 在 <body> 上挂载时退回到其常量值（34px）。
  return createPortal(
    <div
      aria-label={copy.region}
      className="pointer-events-none fixed left-1/2 top-[calc(var(--titlebar-height,34px)+0.75rem)] z-[200] flex w-[min(32rem,calc(100%-2rem))] -translate-x-1/2 flex-col gap-2"
      ref={regionRef}
      role="region"
    >
      <NotificationItem notification={latest} />
      {expanded && olderNotifications.map(n => <NotificationItem key={n.id} notification={n} />)}
      {overflowCount > 0 && (
        <div className={cn(STACK_SURFACE, 'flex min-h-8 items-center justify-between px-3 text-xs')}>
          <button
            className="-ml-1.5 rounded-md px-1.5 py-0.5 font-medium text-white/80 transition hover:bg-white/10 hover:text-white"
            onClick={() => setExpanded(v => !v)}
            type="button"
          >
            {expanded ? copy.hide : copy.show} {copy.more(overflowCount)}
          </button>
          <button
            className="-mr-1.5 rounded-md px-1.5 py-0.5 text-white/60 transition hover:bg-white/10 hover:text-white"
            onClick={clearNotifications}
            type="button"
          >
            {copy.clearAll}
          </button>
        </div>
      )}
    </div>,
    document.body
  )
}

function NotificationItem({ notification }: { notification: AppNotification }): React.JSX.Element {
  const styles = tone[notification.kind]
  const Icon = styles.icon
  const hasDetail = Boolean(notification.detail && notification.detail !== notification.message)
  const t = strings
  const copy = t.notifications

  return (
    <div
      aria-live={notification.kind === 'error' ? 'assertive' : 'polite'}
      className={cn(
        STACK_SURFACE,
        'grid w-full grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-x-2.5 px-3.5 py-2.5'
      )}
      role={notification.kind === 'error' ? 'alert' : 'status'}
    >
      <Icon className={cn('mt-0.5 size-4 shrink-0', styles.iconClass)} />
      <div className="col-start-2 min-w-0">
        {notification.title && (
          <div className="text-xs font-medium tracking-tight text-white">{notification.title}</div>
        )}
        <div className="grid justify-items-start gap-1 text-[11px] leading-relaxed text-white/70">
          <p className="m-0">{notification.message}</p>
          {hasDetail && <NotificationDetail detail={notification.detail || ''} />}
          {notification.action && (
            <button
              className="mt-0.5 rounded-md px-1.5 py-0.5 font-medium text-[#6c8aff] transition hover:bg-[#6c8aff]/15"
              onClick={() => {
                notification.action?.onClick()
                dismissNotification(notification.id)
              }}
              type="button"
            >
              {notification.action.label}
            </button>
          )}
        </div>
      </div>
      <button
        aria-label={copy.dismiss}
        className="col-start-3 mt-0.5 inline-flex size-6 items-center justify-center rounded-md text-white/40 transition hover:bg-white/10 hover:text-white"
        onClick={() => dismissNotification(notification.id)}
        type="button"
      >
        <X className="size-3.5" />
      </button>
    </div>
  )
}

function NotificationDetail({ detail }: { detail: string }): React.JSX.Element {
  const t = strings
  const copy = t.notifications

  return (
    <details className="text-xs text-white/60">
      <summary className="select-none font-medium text-white/60 hover:text-white">{copy.details}</summary>
      <div className="mt-1 rounded-md bg-white/5 p-2">
        <pre className="max-h-32 whitespace-pre-wrap wrap-break-word font-mono text-[0.6875rem] leading-relaxed text-white/70">
          {detail}
        </pre>
        <CopyDetailButton label={copy.copyDetail} text={detail} />
      </div>
    </details>
  )
}

const COPIED_RESET_MS = 1500

function CopyDetailButton({ label, text }: { label: string; text: string }): React.JSX.Element {
  const [copied, setCopied] = useState(false)
  const [failed, setFailed] = useState(false)

  const onClick = () => {
    void (async () => {
      try {
        if (window.spiritagent?.writeClipboard) {
          await window.spiritagent.writeClipboard(text)
        } else {
          await navigator.clipboard.writeText(text)
        }

        triggerHaptic('selection')
        setCopied(true)
        window.setTimeout(() => setCopied(false), COPIED_RESET_MS)
      } catch {
        setFailed(true)
        window.setTimeout(() => setFailed(false), COPIED_RESET_MS)
      }
    })()
  }

  return (
    <button
      className="mt-1 inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[0.6875rem] text-white/60 transition hover:bg-white/10 hover:text-white"
      onClick={onClick}
      type="button"
    >
      {copied ? <Check className="size-3" /> : failed ? <X className="size-3" /> : <Copy className="size-3" />}
      {copied ? strings.common.copied : failed ? strings.common.copyFailed : label}
    </button>
  )
}

export function InlineNotice({
  kind = 'info',
  title,
  children,
  className
}: {
  kind?: NotificationKind
  title?: string
  children: ReactNode
  className?: string
}): React.JSX.Element {
  const styles = tone[kind]
  const Icon = styles.icon

  return (
    <div
      className={cn(
        STACK_SURFACE,
        'grid w-full grid-cols-[auto_minmax(0,1fr)] items-start gap-x-2.5 px-3.5 py-2.5 text-xs',
        className
      )}
      role={kind === 'error' ? 'alert' : 'status'}
    >
      <Icon className={cn('mt-0.5 size-4 shrink-0', styles.iconClass)} />
      <div className="col-start-2 min-w-0">
        {title && <div className="font-medium tracking-tight text-white">{title}</div>}
        <div className={cn('text-[11px] leading-relaxed text-white/70', !title && 'row-start-1')}>{children}</div>
      </div>
    </div>
  )
}
