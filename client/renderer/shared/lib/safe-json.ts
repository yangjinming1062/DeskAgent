/** Parse JSON and fall back to a default on failure or non-object input.
 *
 * Used by the renderer wherever it deserializes a JSON string written by
 * the backend (wardrobe overrides, persona blobs, etc.) — a malformed or
 * missing field should never crash the renderer; it should surface as the
 * default value.
 */
export function safeJsonParse<T>(raw: string | null | undefined, fallback: T): T {
  if (typeof raw !== 'string' || raw.length === 0) {
    return fallback
  }

  try {
    const parsed: unknown = JSON.parse(raw)

    return parsed === null || parsed === undefined ? fallback : (parsed as T)
  } catch {
    return fallback
  }
}
