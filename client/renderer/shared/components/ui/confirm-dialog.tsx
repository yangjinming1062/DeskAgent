'use client'

import * as DialogPrimitive from '@radix-ui/react-dialog'
import { useState } from 'react'

import { strings } from '@/shared/strings'

import { Button } from './button'

interface ConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  confirmLabel: string
  /** 默认为 strings.common.cancel。 */
  cancelLabel?: string
  /** 警示类确认会着色确认按钮并把焦点放在上面。 */
  variant?: 'default' | 'destructive'
  onConfirm: () => void | Promise<void>
}

// 用于警示性确认的小型弹窗（清空 API key、退出登录、重置配置）。
// 替换 hub/settings 中的 window.confirm() ——保留玻璃质感，复用现有 Alert / Button
// 原语，并通过 Radix 维持焦点陷阱与 Esc 关闭。
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  cancelLabel,
  variant = 'default',
  onConfirm
}: ConfirmDialogProps): React.JSX.Element {
  const [busy, setBusy] = useState(false)
  const isDestructive = variant === 'destructive'

  return (
    <DialogPrimitive.Root onOpenChange={busy ? undefined : onOpenChange} open={open}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay
          className="fixed inset-0 z-50 bg-black/40 backdrop-blur-[0.125rem] data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:animate-in data-[state=open]:fade-in-0"
          data-slot="dialog-overlay"
        />
        <DialogPrimitive.Content
          className="fixed top-1/2 left-1/2 z-50 w-[min(28rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-(--ui-stroke-secondary) bg-(--ui-chat-surface-background) p-5 shadow-md data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:animate-in data-[state=open]:fade-in-0"
          data-slot="dialog-content"
          onEscapeKeyDown={event => {
            if (busy) {
              event.preventDefault()
            }
          }}
        >
          <DialogPrimitive.Title className="text-sm font-semibold text-foreground" data-slot="dialog-title">
            {title}
          </DialogPrimitive.Title>
          {description && (
            <DialogPrimitive.Description
              className="mt-2 text-xs leading-relaxed text-(--ui-text-tertiary)"
              data-slot="dialog-description"
            >
              {description}
            </DialogPrimitive.Description>
          )}
          <div className="mt-5 flex justify-end gap-2">
            <Button disabled={busy} onClick={() => onOpenChange(false)} size="sm" variant="outline">
              {cancelLabel ?? strings.common.cancel}
            </Button>
            <Button
              autoFocus={isDestructive}
              disabled={busy}
              onClick={async () => {
                setBusy(true)

                try {
                  await onConfirm()
                  onOpenChange(false)
                } catch {
                  // 出错时保留弹窗，让用户可以重试或取消。
                  // 错误由调用方的 notify / notifyError 抛出，不由弹窗本身处理。
                } finally {
                  setBusy(false)
                }
              }}
              size="sm"
              variant={isDestructive ? 'destructive' : 'default'}
            >
              {confirmLabel}
            </Button>
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
