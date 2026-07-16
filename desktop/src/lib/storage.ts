export function storedBoolean(key: string, fallback: boolean): boolean {
  try {
    const value = window.localStorage.getItem(key)

    return value === null ? fallback : value === 'true'
  } catch {
    return fallback
  }
}

export function persistBoolean(key: string, value: boolean) {
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

export function persistString(key: string, value: null | string) {
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

export function storedStringArray(key: string): string[] {
  try {
    const value = window.localStorage.getItem(key)

    if (!value) {
      return []
    }

    const parsed = JSON.parse(value)

    if (!Array.isArray(parsed)) {
      return []
    }

    return parsed.filter((item): item is string => typeof item === 'string' && item.length > 0)
  } catch {
    return []
  }
}

export function persistStringArray(key: string, value: string[]) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // Best-effort.
  }
}

export function storedStringRecord(key: string): Record<string, string> {
  try {
    const value = window.localStorage.getItem(key)

    if (!value) {
      return {}
    }

    const parsed = JSON.parse(value)

    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return {}
    }

    return Object.fromEntries(
      Object.entries(parsed).filter((entry): entry is [string, string] => typeof entry[1] === 'string')
    )
  } catch {
    return {}
  }
}

export function persistStringRecord(key: string, value: Record<string, string>) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // Best-effort.
  }
}

export function arraysEqual(left: string[], right: string[]) {
  return left.length === right.length && left.every((item, index) => item === right[index])
}

export function insertUniqueId(ids: string[], id: string, index: number) {
  const next = ids.filter(item => item !== id)
  const boundedIndex = Math.min(Math.max(index, 0), next.length)
  next.splice(boundedIndex, 0, id)

  return next
}
