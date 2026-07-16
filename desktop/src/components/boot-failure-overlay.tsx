import { useStore } from '@nanostores/react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { ErrorIcon } from '@/components/ui/error-state'
import { useI18n } from '@/i18n'
import { Loader2, RefreshCw } from '@/lib/icons'
import { $desktopBoot } from '@/store/boot'

type BusyAction = 'retry' | null

export function BootFailureOverlay() {
  const boot = useStore($desktopBoot)
  const { t } = useI18n()
  const [busy, setBusy] = useState<BusyAction>(null)

  const visible = Boolean(boot.error) && !boot.running

  if (!visible) {
    return null
  }

  const retry = async () => {
    setBusy('retry')
    window.location.reload()
  }

  const copy = t.boot.failure

  return (
    <div className="fixed inset-0 z-[1400] flex items-center justify-center bg-(--ui-chat-surface-background) p-6">
      <div className="w-full max-w-[40rem] overflow-hidden rounded-xl border border-(--stroke-zast) bg-(--ui-chat-bubble-background) shadow-zast">
        <div className="flex items-start gap-3 px-5 py-4">
          <ErrorIcon className="mt-0.5" size="1.25rem" />
          <div>
            <h2 className="text-[0.9375rem] font-semibold tracking-tight">{copy.title}</h2>
            <p className="mt-1 text-[0.8125rem] leading-5 text-(--ui-text-tertiary)">{copy.description}</p>
          </div>
        </div>

        <div className="grid gap-4 p-5">
          <div className="rounded-2xl border border-destructive/30 bg-destructive/10 px-4 py-3 text-xs text-destructive">
            {boot.error}
          </div>

          <div className="grid gap-2">
            <div className="flex flex-wrap gap-2">
              <Button disabled={Boolean(busy)} onClick={() => void retry()}>
                {busy === 'retry' ? <Loader2 className="animate-spin" /> : <RefreshCw />}
                {copy.retry}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">{copy.retryHint}</p>
          </div>
        </div>
      </div>
    </div>
  )
}
