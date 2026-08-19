import * as React from 'react'

import { triggerHaptic } from '@/shared/lib/haptics'
import { Check, Copy, X } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { strings } from '@/shared/strings'

import { Button } from './button'
import { DropdownMenuItem } from './dropdown-menu'
import { Tip } from './tooltip'

type CopyPayload = string | (() => Promise<string> | string)
type CopyButtonAppearance = 'button' | 'icon' | 'inline' | 'menu-item' | 'tool-row'
type CopyStatus = 'copied' | 'error' | 'idle'
const COPIED_RESET_MS = 1_500

export async function writeClipboardText(text: string): Promise<void> {
  if (!text) {
    return
  }

  if (window.spiritagent?.writeClipboard) {
    await window.spiritagent.writeClipboard(text)

    return
  }

  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text)

    return
  }

  throw new Error('Clipboard API is unavailable')
}

export interface CopyButtonProps {
  appearance?: CopyButtonAppearance
  buttonSize?: React.ComponentProps<typeof Button>['size']
  buttonVariant?: React.ComponentProps<typeof Button>['variant']
  children?: React.ReactNode
  className?: string
  disabled?: boolean
  errorMessage?: string
  haptic?: boolean
  iconClassName?: string
  label?: string
  onCopied?: () => void
  onCopyError?: (error: unknown) => void
  preventDefault?: boolean
  showLabel?: boolean
  stopPropagation?: boolean
  text: CopyPayload
  title?: string
}

export function CopyButton({
  appearance = 'button',
  buttonSize,
  buttonVariant = 'ghost',
  children,
  className,
  disabled = false,
  errorMessage,
  haptic = true,
  iconClassName,
  label = strings.common.copy,
  onCopied,
  onCopyError,
  preventDefault = false,
  showLabel,
  stopPropagation = false,
  text,
  title
}: CopyButtonProps): React.JSX.Element {
  const t = strings
  const resolvedErrorMessage = errorMessage ?? t.common.copyFailed
  const resolvedLabel = label ?? t.common.copy
  const [status, setStatus] = React.useState<CopyStatus>('idle')
  const resetRef = React.useRef<number | null>(null)

  React.useEffect(() => {
    return () => {
      if (resetRef.current !== null) {
        window.clearTimeout(resetRef.current)
      }
    }
  }, [])

  const copy = React.useCallback(
    async (event?: Event | React.MouseEvent<HTMLElement>) => {
      if (preventDefault) {
        event?.preventDefault()
      }

      if (stopPropagation) {
        event?.stopPropagation()
      }

      try {
        const value = typeof text === 'function' ? await text() : text

        if (!value) {
          return
        }

        await writeClipboardText(value)

        if (haptic) {
          triggerHaptic('selection')
        }

        if (resetRef.current !== null) {
          window.clearTimeout(resetRef.current)
        }

        setStatus('copied')
        resetRef.current = window.setTimeout(() => {
          setStatus('idle')
          resetRef.current = null
        }, COPIED_RESET_MS)
        onCopied?.()
      } catch (error) {
        onCopyError?.(error)

        if (resetRef.current !== null) {
          window.clearTimeout(resetRef.current)
        }

        setStatus('error')
        resetRef.current = window.setTimeout(() => {
          setStatus('idle')
          resetRef.current = null
        }, COPIED_RESET_MS)
      }
    },
    [haptic, onCopied, onCopyError, preventDefault, stopPropagation, text]
  )

  const Icon = status === 'copied' ? Check : status === 'error' ? X : Copy
  const icon = <Icon className={cn('size-3.5', iconClassName)} />

  const visibleChildren =
    (showLabel ?? (appearance !== 'icon' && appearance !== 'tool-row'))
      ? status === 'copied'
        ? t.common.copied
        : status === 'error'
          ? t.common.failed
          : (children ?? resolvedLabel)
      : null

  const content = (
    <>
      {icon}
      {visibleChildren}
    </>
  )

  const feedbackLabel =
    status === 'copied' ? t.common.copied : status === 'error' ? resolvedErrorMessage : (title ?? resolvedLabel)

  const ariaLabel = status === 'idle' ? resolvedLabel : feedbackLabel

  if (appearance === 'menu-item') {
    return (
      <DropdownMenuItem
        className={className}
        disabled={disabled}
        onSelect={event => {
          event.preventDefault()
          void copy(event)
        }}
      >
        {content}
      </DropdownMenuItem>
    )
  }

  if (appearance === 'inline') {
    return (
      <button
        aria-label={ariaLabel}
        className={cn(
          'inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-[0.75rem] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-40',
          className
        )}
        disabled={disabled}
        onClick={event => void copy(event)}
        type="button"
      >
        {content}
      </button>
    )
  }

  if (appearance === 'tool-row') {
    return (
      <Tip label={feedbackLabel}>
        <button
          aria-label={ariaLabel}
          className={cn(
            'grid size-6 place-items-center rounded-md text-muted-foreground/70 opacity-0 transition-opacity hover:bg-accent/55 hover:text-foreground focus-visible:opacity-100 group-hover/tool-row:opacity-100 disabled:opacity-40',
            className
          )}
          disabled={disabled}
          onClick={event => void copy(event)}
          type="button"
        >
          {icon}
        </button>
      </Tip>
    )
  }

  const button = (
    <Button
      aria-label={ariaLabel}
      className={className}
      disabled={disabled}
      onClick={event => void copy(event)}
      size={buttonSize ?? (appearance === 'icon' ? 'icon' : 'default')}
      type="button"
      variant={buttonVariant}
    >
      {content}
    </Button>
  )

  // 只有纯图标按钮需要 tooltip；带文字的变体已经显示了 label。
  return appearance === 'icon' ? <Tip label={feedbackLabel}>{button}</Tip> : button
}
