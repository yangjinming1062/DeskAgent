import { useStore } from '@nanostores/react'
import { type FormEvent, useState } from 'react'

import { Button } from '@/shared/components/ui'
import { Loader2, Sparkles } from '@/shared/lib/icons'
import { $auth, activate } from '@/shared/store/auth'

/**
 * Activation code entry overlay shown in the companion (sprite) window when
 * the user is unauthenticated.  Replaces the old tool-window login page —
 * the user pastes a base64 activation code and the main process exchanges
 * it for a session JWT via ``/api/user/activate``.
 */
export function ActivationOverlay({ onClose }: { onClose: () => void }): React.JSX.Element {
  const auth = useStore($auth)
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)

  const error = auth.kind === 'unauthenticated' ? auth.error : null
  const trimmed = code.trim()

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()

    if (!trimmed || busy) {
      return
    }

    setBusy(true)

    try {
      await activate({ code: trimmed })
      onClose()
    } catch {
      // $auth.error carries the message; the banner reads it.
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[1200] flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <form
        className="deskagent-fade-in w-full max-w-lg rounded-2xl border border-border bg-card p-7 shadow-2xl"
        onSubmit={onSubmit}
      >
        <div className="mb-5 flex items-center gap-3">
          <div className="flex size-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <Sparkles className="size-5" />
          </div>
          <div>
            <h2 className="text-lg font-semibold tracking-tight">激活 DeskAgent</h2>
            <p className="text-sm text-muted-foreground">粘贴您收到的激活码以开始使用。</p>
          </div>
        </div>

        {error && (
          <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {error}
          </div>
        )}

        <textarea
          autoFocus
          className="h-24 w-full resize-none rounded-lg border border-input bg-background px-3 py-2 font-mono text-xs leading-relaxed shadow-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 disabled:opacity-60"
          disabled={busy}
          onChange={e => setCode(e.target.value)}
          placeholder="在此粘贴激活码…"
          spellCheck={false}
          value={code}
        />

        <div className="mt-5 flex justify-end gap-2">
          <Button className="inline-flex items-center gap-2" disabled={!trimmed || busy} type="submit">
            {busy ? <Loader2 className="size-4 animate-spin" /> : null}
            {busy ? '激活中…' : '激活'}
          </Button>
        </div>
      </form>
    </div>
  )
}
