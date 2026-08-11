import type React from 'react'
import { useEffect, useState } from 'react'

import { useAsyncLoader } from '@/shared/hooks/use-async-loader'
import { notifyError } from '@/shared/store/notifications'

type SaveResult = { ok: true } | { ok: false; error: string }

type Patch = {
  path: readonly (string | number)[]
  value?: unknown
  op?: 'set' | 'delete'
}

type Config = Record<string, unknown>

export function getIn(obj: unknown, path: readonly (string | number)[]): unknown {
  let cur: unknown = obj

  for (const key of path) {
    if (cur == null || typeof cur !== 'object') {
      return undefined
    }
    cur = (cur as Record<string | number, unknown>)[key]
  }

  return cur
}

export function setIn(obj: Config, path: readonly (string | number)[], value: unknown): Config {
  if (path.length === 0) {
    return obj
  }
  const [key, ...rest] = path
  const clone = (Array.isArray(obj) ? [...obj] : { ...obj }) as Record<string | number, unknown>
  clone[key] = rest.length === 0 ? value : setIn((clone[key] as Config) ?? {}, rest, value)

  return clone as Config
}

export interface UseRunnerConfigResult {
  config: Config | null
  setConfig: React.Dispatch<React.SetStateAction<Config | null>>
  isLoading: boolean
  write: (content: string) => Promise<SaveResult>
  patch: (p: Patch) => Promise<SaveResult>
}

export function useRunnerConfig(errorKey: string): UseRunnerConfigResult {
  const [config, setConfig] = useState<Config | null>(null)

  const loader = useAsyncLoader<{ ok: boolean; error?: string; content?: string }>(
    () => window.deskagent.runnerConfig.read(),
    errorKey
  )

  useEffect(() => {
    if (loader.error) {
      notifyError(loader.error, errorKey)
    }

    if (loader.data?.ok && typeof loader.data.content === 'string') {
      setConfig(JSON.parse(loader.data.content))
    }
  }, [loader.data, loader.error, errorKey])

  const isLoading = loader.isLoading

  const toWriteResult = (res: Awaited<ReturnType<typeof window.deskagent.runnerConfig.write>>): SaveResult =>
    res.ok ? { ok: true } : { ok: false, error: res.error || 'unknown error' }

  const write = async (content: string): Promise<SaveResult> =>
    toWriteResult(await window.deskagent.runnerConfig.write(content))

  const patch = async (p: Patch): Promise<SaveResult> => toWriteResult(await window.deskagent.runnerConfig.patch(p))

  return { config, setConfig, isLoading, write, patch }
}
