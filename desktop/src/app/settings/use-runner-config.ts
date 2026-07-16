import { useEffect, useState } from 'react'
import { type Document, parseDocument } from 'yaml'

import { notifyError } from '@/store/notifications'

type SaveResult = { ok: true; restarted: boolean; restartError?: string } | { ok: false; error: string }

type Patch = {
  path: readonly (string | number)[]
  value?: unknown
  op?: 'set' | 'delete'
}

/**
 * Shared lifecycle for settings pages that read/write `$ZAST_HOME/config.yaml`
 * via the `zast:runner-config:*` IPC channels.
 *
 * On mount the hook reads the file, parses it into a YAML `Document`, and
 * surfaces a loading / error state. Callers hold the returned `yamlDoc` for
 * read access; mutations should clone the doc, apply their edits, then call
 * either `write(content)` (full-document save) or `patch({...})` (surgical
 * update). Both paths go through the main-process IPC handler that performs
 * deprecated-key cleanup + atomic write + Runner bridge restart.
 */
export function useRunnerConfig(errorKey: string) {
  const [yamlDoc, setYamlDoc] = useState<Document | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    let cancelled = false

    void (async () => {
      try {
        setIsLoading(true)
        const res = await window.zastDesktop.runnerConfig.read()

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

  const write = async (content: string): Promise<SaveResult> => {
    const res = await window.zastDesktop.runnerConfig.write(content)

    return res.ok
      ? { ok: true, restarted: res.restarted !== false, restartError: res.restartError }
      : { ok: false, error: res.error || 'unknown error' }
  }

  const patch = async (p: Patch): Promise<SaveResult> => {
    const res = await window.zastDesktop.runnerConfig.patch(p)

    return res.ok
      ? { ok: true, restarted: res.restarted !== false, restartError: res.restartError }
      : { ok: false, error: res.error || 'unknown error' }
  }

  return { yamlDoc, setYamlDoc, isLoading, write, patch }
}
