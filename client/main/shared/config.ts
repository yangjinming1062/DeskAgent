import fs from 'node:fs'
import path from 'node:path'

// $DESKAGENT_HOME/desktop-config.json holds the user's activated backend URL
// (kept distinct from the encrypted session file at `agent-session.json`
// so it survives a logout). Best-effort: missing / malformed file yields null.
export const FILENAME = 'desktop-config.json'

export function configPath(deskagentHome: string | null | undefined): string | null {
  if (!deskagentHome) {
    return null
  }

  return path.join(deskagentHome, FILENAME)
}

export function readStoredBackendUrl(deskagentHome: string | null | undefined): string | null {
  const target = configPath(deskagentHome)

  if (!target) {
    return null
  }

  try {
    const parsed = JSON.parse(fs.readFileSync(target, 'utf8'))

    if (parsed && typeof parsed.backendUrl === 'string' && parsed.backendUrl.trim()) {
      return parsed.backendUrl.trim()
    }
  } catch {
    // missing / malformed / unreadable
  }

  return null
}

export function writeStoredBackendUrl(deskagentHome: string | null | undefined, backendUrl: string): boolean {
  const target = configPath(deskagentHome)

  if (!target || typeof backendUrl !== 'string' || !backendUrl.trim()) {
    return false
  }

  let existing: Record<string, unknown> = {}

  try {
    existing = JSON.parse(fs.readFileSync(target, 'utf8'))

    if (!existing || typeof existing !== 'object') {
      existing = {}
    }
  } catch {
    existing = {}
  }

  existing.backendUrl = backendUrl.trim()
  existing.savedAt = Date.now()

  try {
    fs.mkdirSync(path.dirname(target), { recursive: true })
    const tmp = `${target}.${process.pid}.${Date.now()}.tmp`
    fs.writeFileSync(tmp, JSON.stringify(existing, null, 2), 'utf8')
    fs.renameSync(tmp, target)

    if (process.platform !== 'win32') {
      try {
        fs.chmodSync(target, 0o600)
      } catch {
        // best-effort; FS may not support chmod
      }
    }

    return true
  } catch {
    return false
  }
}

// Resolves the active backend URL stored in $DESKAGENT_HOME/desktop-config.json
// (written on successful activation code exchange).
// Returns null if the client has not been activated yet.
export function resolveBackendUrl(deskagentHome: string | null | undefined): string | null {
  return readStoredBackendUrl(deskagentHome)
}

// Coerce + trailing-slash strip for callers appending path suffixes (e.g. /api/update).
// Returns null if no backend URL is configured.
export function resolveNormalizedBackendUrl(deskagentHome: string | null | undefined): string | null {
  const url = resolveBackendUrl(deskagentHome)

  return url ? String(url).replace(/\/+$/, '') : null
}
