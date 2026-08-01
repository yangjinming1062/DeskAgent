import { useStore } from '@nanostores/react'
import { type FormEvent, useEffect, useState } from 'react'

import { BrandMark } from '@/shared/components/brand-mark'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Loader2, LogIn } from '@/shared/lib/icons'
import { $auth, login } from '@/shared/store/auth'
import { strings } from '@/shared/strings'

export function LoginPage() {
  const t = strings
  const a = t.login
  const auth = useStore($auth)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [backendUrl, setBackendUrl] = useState('')
  const [busy, setBusy] = useState(false)

  // Prefill the backend URL from the persisted login default (set on
  // successful login) so re-login after a logout keeps the same target.
  // On a fresh install with no desktop-config.json, the persisted default
  // falls back to the bundled config.json entry via main's resolver.
  useEffect(() => {
    let cancelled = false
    window.deskagent
      .getDefaultBackendUrl()
      .then(value => {
        if (!cancelled && typeof value === 'string') setBackendUrl(value)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [])

  const error = auth.kind === 'unauthenticated' ? auth.error : undefined

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()

    if (busy) return
    setBusy(true)

    try {
      await login({ username, password, baseUrl: backendUrl.trim() || undefined })
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
          <label className="text-xs font-medium" htmlFor="login-backend-url">
            {a.backendUrl}
          </label>
          <Input
            autoComplete="url"
            disabled={busy}
            id="login-backend-url"
            inputMode="url"
            onChange={event => setBackendUrl(event.currentTarget.value)}
            placeholder={a.backendUrlPlaceholder}
            required
            spellCheck={false}
            type="url"
            value={backendUrl}
          />
        </div>

        <div className="grid gap-1.5">
          <label className="text-xs font-medium" htmlFor="login-username">
            {a.username}
          </label>
          <Input
            autoComplete="username"
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

        <Button
          disabled={busy || !username || !password || !backendUrl.trim()}
          type="submit"
        >
          {busy ? <Loader2 className="size-3.5 animate-spin" /> : <LogIn className="size-3.5" />}
          {busy ? a.signingIn : a.signIn}
        </Button>
      </form>
    </div>
  )
}
