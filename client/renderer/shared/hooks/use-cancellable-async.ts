import { type DependencyList, useEffect } from 'react'

// Wraps the `let cancelled = false` + useEffect + try/finally pattern that
// every async-loading hook in the renderer hand-rolls. The effect runs the
// async callback on mount and on every `deps` change; if a newer run is
// kicked off (or the component unmounts), `isStale()` flips so the caller
// can drop the result before setState. The returned promise resolves to
// the callback's result if still-current, or `undefined` if a newer run
// (or unmount) superseded it.
//
// Note: this does NOT abort the underlying operation. It only suppresses
// the post-await side-effects. For true cancellation wire the fetch into
// AbortController and pass the signal into the fetcher.
export function useCancellableAsync<T>(effect: (isStale: () => boolean) => Promise<T>, deps: DependencyList): void {
  useEffect(() => {
    let cancelled = false

    void effect(() => cancelled)

    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
}
