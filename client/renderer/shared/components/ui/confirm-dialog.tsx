'use client'

import * as RadixUI from 'radix-ui'
import { useState } from 'react'

import { strings } from '@/shared/strings'

import { Button } from './button'

const DialogPrimitive = RadixUI.Dialog

interface ConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  confirmLabel: string
  /** Defaults to strings.common.cancel. */
  cancelLabel?: string
  /** Destructive confirms colour the confirm button + apply focus to it. */
  variant?: 'default' | 'destructive'
  onConfirm: () => void | Promise<void>
}

// Small modal for destructive confirms (clear API key, sign out, reset
// config). Replaces window.confirm() in hub/settings — keeps the glass
// aesthetic, integrates with the existing Alert/Button primitives, and
// stays focus-trapped + Esc-dismissible via Radix.
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
                  // Leave the dialog open on error so the user can retry
                  // or cancel. Errors are surfaced by the caller's
                  // notify/notifyError, not the dialog itself.
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
