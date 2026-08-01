import { type CSSProperties, type FormEvent, useEffect, useState } from 'react'
import { useStore } from '@nanostores/react'
import { ArrowRight, AlertCircle, CheckCircle2, Loader2 } from 'lucide-react'

import { Button } from '../components/button'
import { $auth, authenticateBackend, startInstall, verifyBackendUrl } from '../store'

const DEFAULT_BACKEND_URL = 'http://localhost:8000'

export default function Auth() {
  const auth = useStore($auth)
  const [backendUrl, setBackendUrl] = useState(DEFAULT_BACKEND_URL)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [reachability, setReachability] = useState<'idle' | 'checking' | 'ok' | 'unreachable'>('idle')

  // Probe the backend as the user edits the URL so the submit button only
  // enables when both fields are filled AND the backend is reachable.
  useEffect(() => {
    if (!backendUrl.trim()) {
      setReachability('idle')
      return
    }
    let cancelled = false
    setReachability('checking')
    const handle = setTimeout(() => {
      verifyBackendUrl(backendUrl)
        .then(ok => {
          if (!cancelled) setReachability(ok ? 'ok' : 'unreachable')
        })
        .catch(() => {
          if (!cancelled) setReachability('unreachable')
        })
    }, 400)
    return () => {
      cancelled = true
      clearTimeout(handle)
    }
  }, [backendUrl])

  const submitting = auth.status === 'submitting'
  const reachable = reachability === 'ok'
  const canSubmit =
    !submitting && reachable && username.trim().length > 0 && password.length > 0

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault()
    if (!canSubmit) return
    try {
      await authenticateBackend(backendUrl.trim(), username.trim(), password)
      // Auth wrote the one-shot bootstrap file; kick off the install
      // protocol. On a re-install the bootstrap file gets overwritten
      // on success — desktop then refreshes the JWT at startup.
      await startInstall()
    } catch {
      // $auth.error carries the message; the banner reads it.
    }
  }

  return (
    <div className="deskagent-fade-in flex h-full flex-col items-center justify-center gap-8 px-12 py-10">
      <div className="w-full max-w-xl min-w-0 text-center">
        <p
          className="fit-text mx-auto mb-3 w-full font-['Collapse'] font-bold uppercase leading-[0.9] tracking-[0.08em] text-midground mix-blend-plus-lighter dark:text-foreground/90"
          style={
            {
              '--fit-text-line-height': '0.9',
              '--fit-text-max': '4rem',
              '--fit-text-min': '2rem'
            } as CSSProperties
          }
        >
          <span>
            <span>登录 DeskAgent</span>
          </span>
          <span aria-hidden="true">登录 DeskAgent</span>
        </p>

        <p className="m-0 text-center text-base leading-normal tracking-tight text-muted-foreground">
          输入 DeskAgent 后端地址和账户信息,安装完成后桌面端将自动登录。
        </p>
      </div>

      <form
        className="grid w-full max-w-xl gap-4 rounded-xl border border-border bg-card p-6 shadow-sm"
        onSubmit={onSubmit}
      >
        {auth.error && (
          <div
            role="alert"
            className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive"
          >
            <AlertCircle size={16} className="mt-0.5 shrink-0" />
            <span className="min-w-0 break-words">{auth.error}</span>
          </div>
        )}

        <div className="grid gap-1.5">
          <label className="text-xs font-medium" htmlFor="auth-backend-url">
            后端地址
          </label>
          <div className="relative">
            <input
              id="auth-backend-url"
              type="url"
              inputMode="url"
              autoComplete="url"
              spellCheck={false}
              required
              disabled={submitting}
              value={backendUrl}
              onChange={event => setBackendUrl(event.currentTarget.value)}
              className="w-full rounded-md border border-input bg-background px-3 py-2 pr-10 text-sm shadow-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 disabled:opacity-60"
              placeholder="https://api.example.com"
            />
            <div className="pointer-events-none absolute inset-y-0 right-2 flex items-center">
              {reachability === 'checking' && (
                <Loader2 size={14} className="animate-spin text-muted-foreground" />
              )}
              {reachability === 'ok' && (
                <CheckCircle2 size={14} className="text-emerald-500" />
              )}
              {reachability === 'unreachable' && (
                <AlertCircle size={14} className="text-destructive" />
              )}
            </div>
          </div>
          {reachability === 'unreachable' && (
            <p className="text-xs text-destructive">无法连接该地址,请检查网络或地址。</p>
          )}
        </div>

        <div className="grid gap-1.5">
          <label className="text-xs font-medium" htmlFor="auth-username">
            用户名
          </label>
          <input
            id="auth-username"
            type="text"
            autoComplete="username"
            autoFocus
            required
            disabled={submitting}
            value={username}
            onChange={event => setUsername(event.currentTarget.value)}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 disabled:opacity-60"
          />
        </div>

        <div className="grid gap-1.5">
          <label className="text-xs font-medium" htmlFor="auth-password">
            密码
          </label>
          <input
            id="auth-password"
            type="password"
            autoComplete="current-password"
            required
            disabled={submitting}
            value={password}
            onChange={event => setPassword(event.currentTarget.value)}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 disabled:opacity-60"
          />
          <p className="text-xs text-muted-foreground">
            密码仅用于本次登录,不会被保存到磁盘。
          </p>
        </div>

        <Button type="submit" disabled={!canSubmit} className="inline-flex items-center gap-2">
          {submitting ? <Loader2 size={16} className="animate-spin" /> : <ArrowRight size={16} />}
          {submitting ? '登录中…' : '登录并安装'}
        </Button>
      </form>
    </div>
  )
}
