import { useEffect, useRef } from 'react'

// Stable ref whose `.current` mirrors the latest `value` across renders — used
// by async callbacks that would otherwise capture a stale closure.
//
// Implementation mirrors the canonical pattern from `react-use/useLatest` and
// `react-router` internals: assign on every render (idiomatic for hooks that
// are referenced from event handlers, intervals, promises) without forcing a
// re-render.
export function useLatestRef<T>(value: T): { readonly current: T } {
  const ref = useRef(value)

  useEffect(() => {
    ref.current = value
  }, [value])

  return ref
}
