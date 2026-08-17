// Three-state secret-field writer used by account form save
// paths. The backend distinguishes:
//
//   1. key absent in the PATCH body → keep existing value untouched
//   2. key present, value = `clearedSentinel` → drop the stored value
//   3. key present, value = any other string → store it
//
// `value === ''` is treated as "untouched" so an unmodified textbox doesn't
// clobber a stored credential with an empty string. Callers that want
// "explicitly empty" should toggle `cleared = true` instead.
export type SecretFieldBody<T> = { omit: true } | { omit: false; value: T }

export function buildSecretFieldBody<T>(value: string, cleared: boolean, clearedSentinel: T): SecretFieldBody<T> {
  if (cleared) {
    return { omit: false, value: clearedSentinel }
  }

  if (value === '') {
    return { omit: true }
  }

  return { omit: false, value: value as unknown as T }
}
