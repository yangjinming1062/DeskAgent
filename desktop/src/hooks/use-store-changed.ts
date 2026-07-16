import { useStore } from '@nanostores/react'
import type { ReadableAtom } from 'nanostores'
import { useEffect, useRef } from 'react'

// Run `callback` once per change to a nanostore value. The first render
// (where the ref is initialized to the current value) is skipped — useful
// for "respond to the bump" patterns (fresh-session requests, profile swaps).
export function useStoreChanged<T>(store: ReadableAtom<T>, callback: (value: T) => void): T {
  const value = useStore(store)
  const lastRef = useRef(value)

  useEffect(() => {
    if (value === lastRef.current) {
      return
    }

    lastRef.current = value
    callback(value)
  }, [callback, value])

  return value
}
