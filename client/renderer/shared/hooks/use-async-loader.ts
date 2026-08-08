import { useEffect, useState } from 'react'

// Canonical "fetch on mount, hold the result, expose error" loop. Replaces
// the ~12-line `useEffect` + `useRef('cancelled')` + try/catch/finally dance
// repeated across every settings page. Caller passes a `load` function that
// resolves to the data (or throws); the hook handles mounting, cancellation
// on unmount, error state, and a `reload` trigger.

export type AsyncLoader<T> = {
  data: T | null
  isLoading: boolean
  error: unknown
  reload: () => void
}

export function useAsyncLoader<T>(load: () => Promise<T>, deps: ReadonlyArray<unknown> = []): AsyncLoader<T> {
  const [data, setData] = useState<T | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [version, setVersion] = useState(0)

  useEffect(() => {
    let cancelled = false

    setIsLoading(true)
    setError(null)
    load()
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
    // Caller-provided `deps` already capture any reactive state the `load` fn
    // closes over; `version` is the explicit manual-reload trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, version])

  return { data, isLoading, error, reload: () => setVersion(v => v + 1) }
}
