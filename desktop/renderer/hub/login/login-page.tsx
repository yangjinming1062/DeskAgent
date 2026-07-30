import { useStore } from '@nanostores/react'
import { type FormEvent, useState } from 'react'

import { BrandMark } from '@/shared/components/brand-mark'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { useI18n } from '@/shared/i18n'
import { Loader2, LogIn } from '@/shared/lib/icons'
import { $auth, login } from '@/shared/store/auth'

export function LoginPage() {
  const { t } = useI18n()
  const a = t.login
  const auth = useStore($auth)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)

  const error = auth.kind === 'unauthenticated' ? auth.error : undefined

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()

    if (busy) {
      return
    }

    setBusy(true)

    try {
      await login({ username, password })
    } catch {
      // $auth.error is set by the store; the banner reads it.
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[1300] flex items-center justify-center bg-(--ui-chat-surface-background) p-6">
      <form
        className="grid w-full max-w-[24rem] gap-4 rounded-xl border border-(--stroke-deskagent) bg-(--ui-chat-bubble-background) p-5 shadow-deskagent"
        onSubmit={onSubmit}
      >
        <BrandMark className="mx-auto size-14" />
        <div className="grid gap-1 text-center">
          <h2 className="text-[0.9375rem] font-semibold tracking-tight">{a.title}</h2>
          <p className="text-[0.8125rem] leading-5 text-(--ui-text-tertiary)">{a.subtitle}</p>
        </div>

        {error && (
          <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {error}
          </div>
        )}

        <div className="grid gap-1.5">
          <label className="text-xs font-medium" htmlFor="login-username">
            {a.username}
          </label>
          <Input
            autoComplete="username"
            autoFocus
            disabled={busy}
            id="login-username"
            onChange={event => setUsername(event.currentTarget.value)}
            required
            type="text"
            value={username}
          />
        </div>
        <div className="grid gap-1.5">
          <label className="text-xs font-medium" htmlFor="login-password">
            {a.password}
          </label>
          <Input
            autoComplete="current-password"
            disabled={busy}
            id="login-password"
            onChange={event => setPassword(event.currentTarget.value)}
            required
            type="password"
            value={password}
          />
        </div>

        <Button disabled={busy || !username || !password} type="submit">
          {busy ? <Loader2 className="size-3.5 animate-spin" /> : <LogIn className="size-3.5" />}
          {busy ? a.signingIn : a.signIn}
        </Button>
      </form>
    </div>
  )
}
