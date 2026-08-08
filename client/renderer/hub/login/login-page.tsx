import { useStore } from '@nanostores/react'
import { type FormEvent, useEffect, useState } from 'react'

import { BrandMark } from '@/shared/components/brand-mark'
import { InlineNotice } from '@/shared/components/notifications'
import { Button, Input } from '@/shared/components/ui'
import { useAsyncLoader } from '@/shared/hooks/use-async-loader'
import { Loader2, LogIn } from '@/shared/lib/icons'
import { $auth, login } from '@/shared/store/auth'
import { strings } from '@/shared/strings'

export function LoginPage(): React.JSX.Element {
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
  const urlLoader = useAsyncLoader<string | null>(async () => {
    const value = await window.deskagent.getDefaultBackendUrl()

    return typeof value === 'string' ? value : null
  })

  useEffect(() => {
    if (typeof urlLoader.data === 'string') {
      setBackendUrl(urlLoader.data)
    }
  }, [urlLoader.data])

  const error = auth.kind === 'unauthenticated' ? auth.error : undefined

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()

    if (busy) {
      return
    }

    setBusy(true)

    try {
      await login({ username, password, baseUrl: backendUrl.trim() || undefined })
    } catch {
      // $auth.error is set by the store; the InlineNotice reads it.
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[1300] flex items-center justify-center bg-(--ui-chat-surface-background) p-6">
      {/* Ambient companion glow — breathes in lockstep with the egg (see
          .login-glow in styles.css). */}
      <span aria-hidden="true" className="login-glow" />
      <form
        className="relative z-10 grid w-full max-w-[25rem] gap-5 rounded-xl border border-(--stroke-deskagent) bg-(--ui-chat-bubble-background) p-7 shadow-deskagent"
        onSubmit={onSubmit}
      >
        <header className="grid justify-items-center gap-2 text-center">
          <BrandMark className="size-16" />
          <h1 className="text-[1.0625rem] font-semibold tracking-tight text-(--ui-text-primary)">{a.title}</h1>
          <p className="text-[0.8125rem] leading-5 text-(--ui-text-tertiary)">{a.subtitle}</p>
        </header>

        {error && <InlineNotice kind="error">{error}</InlineNotice>}

        <div className="grid gap-4">
          <div className="grid gap-1.5">
            <label className="text-xs font-medium text-(--ui-text-secondary)" htmlFor="login-backend-url">
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
            <label className="text-xs font-medium text-(--ui-text-secondary)" htmlFor="login-username">
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
            <label className="text-xs font-medium text-(--ui-text-secondary)" htmlFor="login-password">
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
        </div>

        <Button className="h-9 w-full" disabled={busy || !username || !password || !backendUrl.trim()} type="submit">
          {busy ? <Loader2 className="size-3.5 animate-spin" /> : <LogIn className="size-3.5" />}
          {busy ? a.signingIn : a.signIn}
        </Button>
      </form>
    </div>
  )
}
