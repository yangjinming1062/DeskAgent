import { type DependencyList, useEffect } from 'react'

type ListenerName = {
  [K in keyof Window['spiritagent']]: NonNullable<Window['spiritagent'][K]> extends (...args: never[]) => unknown
    ? K
    : never
}[keyof Window['spiritagent']]

export function useMainProcessListener<K extends ListenerName>(
  name: K,
  fn: Parameters<NonNullable<Window['spiritagent'][K]>>[0],
  deps: DependencyList
): void {
  useEffect(() => {
    const sub = window.spiritagent?.[name] as ((cb: typeof fn) => (() => void) | undefined) | undefined
    const off = sub?.(fn)

    return () => {
      off?.()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- caller-managed deps
  }, deps)
}
