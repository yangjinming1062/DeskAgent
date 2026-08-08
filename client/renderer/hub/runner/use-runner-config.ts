import type React from 'react'
import { useEffect, useState } from 'react'
import { type Document, parseDocument } from 'yaml'

import { notifyError } from '@/shared/store/notifications'

type SaveResult = { ok: true; restarted: boolean; restartError?: string } | { ok: false; error: string }

type Patch = {
  path: readonly (string | number)[]
  value?: unknown
  op?: 'set' | 'delete'
}

export interface UseRunnerConfigResult {
  yamlDoc: Document | null
  setYamlDoc: React.Dispatch<React.SetStateAction<Document | null>>
  isLoading: boolean
  write: (content: string) => Promise<SaveResult>
  patch: (p: Patch) => Promise<SaveResult>
}

/**
 * Shared lifecycle for settings pages that read/write `$DESKAGENT_HOME/config.yaml`
 * via the `deskagent:runner-config:*` IPC channels.
 *
 * On mount the hook reads the file, parses it into a YAML `Document`, and
 * surfaces a loading / error state. Callers hold the returned `yamlDoc` for
 * read access; mutations should clone the doc, apply their edits, then call
 * either `write(content)` (full-document save) or `patch({...})` (surgical
 * update). Both paths go through the main-process IPC handler that performs
 * deprecated-key cleanup + atomic write + Runner bridge restart.
 */
export function useRunnerConfig(errorKey: string): UseRunnerConfigResult {
  const [yamlDoc, setYamlDoc] = useState<Document | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    let cancelled = false

    void (async () => {
      try {
        setIsLoading(true)
        const res = await window.deskagent.runnerConfig.read()

        if (cancelled) {
          return
        }

        if (!res.ok) {
          throw new Error(res.error)
        }

        setYamlDoc(parseDocument(res.content || ''))
      } catch (err) {
        if (!cancelled) {
          notifyError(err, errorKey)
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false)
        }
      }
    })()

    return () => {
      cancelled = true
    }
  }, [errorKey])

  const toWriteResult = (res: Awaited<ReturnType<typeof window.deskagent.runnerConfig.write>>): SaveResult =>
    res.ok
      ? { ok: true, restarted: res.restarted !== false, restartError: res.restartError }
      : { ok: false, error: res.error || 'unknown error' }

  const write = async (content: string): Promise<SaveResult> =>
    toWriteResult(await window.deskagent.runnerConfig.write(content))

  const patch = async (p: Patch): Promise<SaveResult> =>
    toWriteResult(await window.deskagent.runnerConfig.patch(p))

  return { yamlDoc, setYamlDoc, isLoading, write, patch }
}
