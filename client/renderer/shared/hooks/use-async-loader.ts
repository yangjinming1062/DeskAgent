import { useCallback, useEffect, useRef, useState } from 'react'

// Canonical "fetch on mount, hold the result, expose error" loop. Replaces
// the ~12-line `useEffect` + `useRef('cancelled')` + try/catch/finally dance
// repeated across every settings page. Caller passes a `load` function that
// resolves to the data (or throws); the hook handles mounting, cancellation
// on unmount, error state, and a manual `reload` trigger.
//
// The `reloadKey` argument lets a caller re-trigger the load when their
// upstream inputs change (e.g. a `errorKey` prop or the user pressing Retry).
// Pass an empty string / 0 / null to load once on mount only.

export type AsyncLoader<T> = {
  data: T | null
  isLoading: boolean
  error: unknown
  reload: () => void
}

export function useAsyncLoader<T>(load: () => Promise<T>, reloadKey?: unknown): AsyncLoader<T> {
  const [data, setData] = useState<T | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [version, setVersion] = useState(0)
  const loadRef = useRef(load)

  // Mirror the latest `load` closure so the effect body always reads fresh
  // state without depending on the function identity.
  loadRef.current = load

  useEffect(() => {
    let cancelled = false

    setIsLoading(true)
    setError(null)

    const promise = loadRef.current()

    promise
      .then(result => {
        if (!cancelled) {
          setData(result)
          setIsLoading(false)
        }
      })
      .catch(err => {
        if (!cancelled) {
          setError(err)
          setIsLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
    // `reloadKey` is the caller-provided discriminator; `version` is the
    // manual-reload trigger (bump via `reload()`).
  }, [reloadKey, version])

  const reload = useCallback(() => {
    setVersion(v => v + 1)
  }, [])

  return { data, isLoading, error, reload }
}
