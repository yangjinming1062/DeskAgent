export function storedBoolean(key: string, fallback: boolean): boolean {
  try {
    const value = window.localStorage.getItem(key)

    return value === null ? fallback : value === 'true'
  } catch {
    return fallback
  }
}

export function persistBoolean(key: string, value: boolean): void {
  try {
    window.localStorage.setItem(key, String(value))
  } catch {
    // Best-effort: restricted contexts (e.g. private browsing) may throw.
  }
}

export function storedString(key: string): null | string {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

export function persistString(key: string, value: null | string): void {
  try {
    if (value === null) {
      window.localStorage.removeItem(key)
    } else {
      window.localStorage.setItem(key, value)
    }
  } catch {
    // Best-effort.
  }
}
