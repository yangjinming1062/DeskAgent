'use strict'

const path = require('node:path')
const os = require('node:os')

/**
 * Canonical $DESKAGENT_HOME for the user. Mirrors `installer/src-tauri/src/paths.rs::deskagent_home`
 * and `runner/utils/constants.py::get_deskagent_home()`:
 *   - $DESKAGENT_HOME env var, if set, wins
 *   - Windows: %LOCALAPPDATA%\deskagent (or ~/.deskagent legacy fallback when `directoryExists` is provided)
 *   - macOS / other POSIX: ~/.deskagent
 *
 * Returns absolute path. Falls back to a writable homedir-derived path even
 * if the env var resolves to a non-existent directory — callers should not
 * assume the directory exists yet on first run.
 *
 * @param {{ directoryExists?: (p: string) => boolean }} [opts]
 *   Pass `directoryExists` to opt in to the Windows legacy-`~/.deskagent` migration:
 *   when no `%LOCALAPPDATA%\deskagent` exists yet but `~/.deskagent` does, prefer the
 *   legacy path so existing users don't lose state. Pass nothing to get the
 *   plain canonical resolver (used by the runner-side processes that don't
 *   care about the migration).
 */
function deskagentHome({ directoryExists } = {}) {
  if (process.env.DESKAGENT_HOME) {
    return path.resolve(process.env.DESKAGENT_HOME)
  }
  if (process.platform === 'win32') {
    const local = process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local')
    const localappdata = path.join(local, 'deskagent')
    if (directoryExists && !directoryExists(localappdata)) {
      const legacy = path.join(os.homedir(), '.deskagent')
      if (directoryExists(legacy)) return legacy
    }
    return localappdata
  }
  return path.join(os.homedir(), '.deskagent')
}

module.exports = { deskagentHome }
