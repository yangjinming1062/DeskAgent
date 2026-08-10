import type React from 'react'
import { useEffect, useState } from 'react'
import { type Document, parseDocument } from 'yaml'

import { useAsyncLoader } from '@/shared/hooks/use-async-loader'
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
 * Shared lifecycle for settings pages that read/write the runner config
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

  const loader = useAsyncLoader<{ ok: boolean; error?: string; content?: string }>(
    () => window.deskagent.runnerConfig.read(),
    errorKey
  )

  useEffect(() => {
    if (loader.error) {
      notifyError(loader.error, errorKey)
    }

    if (loader.data?.ok && typeof loader.data.content === 'string') {
      setYamlDoc(parseDocument(loader.data.content))
    }
  }, [loader.data, loader.error, errorKey])

  const isLoading = loader.isLoading

  const toWriteResult = (res: Awaited<ReturnType<typeof window.deskagent.runnerConfig.write>>): SaveResult =>
    res.ok
      ? { ok: true, restarted: res.restarted !== false, restartError: res.restartError }
      : { ok: false, error: res.error || 'unknown error' }

  const write = async (content: string): Promise<SaveResult> =>
    toWriteResult(await window.deskagent.runnerConfig.write(content))

  const patch = async (p: Patch): Promise<SaveResult> => toWriteResult(await window.deskagent.runnerConfig.patch(p))

  return { yamlDoc, setYamlDoc, isLoading, write, patch }
}
