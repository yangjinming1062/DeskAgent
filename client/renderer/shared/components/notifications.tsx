import { useStore } from '@nanostores/react'
import { type ReactNode, type Ref, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import { triggerHaptic } from '@/shared/lib/haptics'
import { AlertCircle, AlertTriangle, CheckCircle2, type IconComponent, Info } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import {
  $notifications,
  type AppNotification,
  clearNotifications,
  dismissNotification,
  type NotificationKind
} from '@/shared/store/notifications'
import { strings } from '@/shared/strings'

import { Alert, AlertDescription, AlertTitle, Button, Codicon, CopyButton } from './ui'

type ToneVariant = 'default' | 'destructive' | 'warning' | 'success'

const tone: Record<NotificationKind, { icon: IconComponent; iconClass: string; variant: ToneVariant }> = {
  error: { icon: AlertCircle, iconClass: 'text-destructive', variant: 'destructive' },
  warning: { icon: AlertTriangle, iconClass: 'text-primary', variant: 'warning' },
  info: { icon: Info, iconClass: 'text-muted-foreground', variant: 'default' },
  success: { icon: CheckCircle2, iconClass: 'text-primary', variant: 'success' }
}

const STACK_SURFACE =
  'pointer-events-auto border border-(--stroke-spiritagent) bg-popover/95 shadow-spiritagent backdrop-blur-md'

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
        <div className={cn(STACK_SURFACE, 'flex min-h-8 items-center justify-between rounded-lg px-3 text-xs')}>
          <Button
            className="-ml-2 font-medium"
            onClick={() => setExpanded(v => !v)}
            size="xs"
            type="button"
            variant="text"
          >
            {expanded ? copy.hide : copy.show} {copy.more(overflowCount)}
          </Button>
          <Button className="-mr-2" onClick={clearNotifications} size="xs" type="button" variant="text">
            {copy.clearAll}
          </Button>
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
    <Alert
      aria-live={notification.kind === 'error' ? 'assertive' : 'polite'}
      className={cn(STACK_SURFACE, 'grid-cols-[auto_minmax(0,1fr)_auto] pr-2.5')}
      role={notification.kind === 'error' ? 'alert' : 'status'}
      variant="default"
    >
      <Icon className={styles.iconClass} />
      <div className="col-start-2 min-w-0">
        {notification.title && <AlertTitle className="col-start-auto">{notification.title}</AlertTitle>}
        <AlertDescription className="col-start-auto">
          <p className="m-0">{notification.message}</p>
          {hasDetail && <NotificationDetail detail={notification.detail || ''} />}
          {notification.action && (
            <Button
              className="mt-1.5 bg-primary/15 font-medium text-primary hover:bg-primary/25 hover:text-primary"
              onClick={() => {
                notification.action?.onClick()
                dismissNotification(notification.id)
              }}
              size="xs"
              type="button"
              variant="ghost"
            >
              {notification.action.label}
            </Button>
          )}
        </AlertDescription>
      </div>
      <Button
        aria-label={copy.dismiss}
        className="col-start-3 -mr-1 text-muted-foreground"
        onClick={() => dismissNotification(notification.id)}
        size="icon-xs"
        type="button"
        variant="ghost"
      >
        <Codicon name="close" size="0.875rem" />
      </Button>
    </Alert>
  )
}

function NotificationDetail({ detail }: { detail: string }): React.JSX.Element {
  const t = strings
  const copy = t.notifications

  return (
    <details className="mt-2 text-xs text-muted-foreground">
      <summary className="select-none font-medium text-muted-foreground hover:text-foreground">{copy.details}</summary>
      <div className="mt-1 rounded-md bg-background/65 p-2">
        <pre className="max-h-32 whitespace-pre-wrap wrap-break-word font-mono text-[0.6875rem] leading-relaxed">
          {detail}
        </pre>
        <CopyButton
          appearance="inline"
          className="mt-1 inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[0.6875rem] text-muted-foreground hover:bg-accent hover:text-foreground"
          errorMessage={copy.copyDetailFailed}
          iconClassName="size-3"
          label={copy.copyDetail}
          text={detail}
        >
          {copy.copyDetail}
        </CopyButton>
      </div>
    </details>
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
    <Alert className={cn('min-w-0', className)} role={kind === 'error' ? 'alert' : 'status'} variant={styles.variant}>
      <Icon />
      {title && <AlertTitle>{title}</AlertTitle>}
      <AlertDescription className={cn(!title && 'row-start-1')}>{children}</AlertDescription>
    </Alert>
  )
}
