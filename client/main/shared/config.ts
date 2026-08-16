import fs from 'node:fs'
import path from 'node:path'

// $SPIRITAGENT_HOME/desktop-config.json holds the user's activated backend URL
// (kept distinct from the encrypted session file at `agent-session.json`
// so it survives a logout). Best-effort: missing / malformed file yields null.
export const FILENAME = 'desktop-config.json'

export function configPath(spiritagentHome: string | null | undefined): string | null {
  if (!spiritagentHome) {
    return null
  }

  return path.join(spiritagentHome, FILENAME)
}

export function readStoredBackendUrl(spiritagentHome: string | null | undefined): string | null {
  const target = configPath(spiritagentHome)

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

export function writeStoredBackendUrl(spiritagentHome: string | null | undefined, backendUrl: string): boolean {
  const target = configPath(spiritagentHome)

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

// Resolves the active backend URL stored in $SPIRITAGENT_HOME/desktop-config.json
// (written on successful activation code exchange).
// Returns null if the client has not been activated yet.
export function resolveBackendUrl(spiritagentHome: string | null | undefined): string | null {
  return readStoredBackendUrl(spiritagentHome)
}

// Coerce + trailing-slash strip for callers appending path suffixes (e.g. /api/update).
// Returns null if no backend URL is configured.
export function resolveNormalizedBackendUrl(spiritagentHome: string | null | undefined): string | null {
  const url = resolveBackendUrl(spiritagentHome)

  return url ? String(url).replace(/\/+$/, '') : null
}
