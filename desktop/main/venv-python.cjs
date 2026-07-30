'use strict'

const path = require('node:path')

/**
 * Path to the python executable inside the venv at `$DESKAGENT_HOME/runner/.venv`.
 * Cross-platform: `Scripts/python.exe` on Windows, `bin/python` elsewhere.
 *
 * @param {string} deskagentHome
 * @param {string} [platform] — defaults to `process.platform`; pass `process.platform` from callers
 *   for testability.
 */
function venvPythonFor(deskagentHome, platform = process.platform) {
  return platform === 'win32'
    ? path.join(deskagentHome, 'runner', '.venv', 'Scripts', 'python.exe')
    : path.join(deskagentHome, 'runner', '.venv', 'bin', 'python')
}

/**
 * Resolve the runner's venv Python + server.py from $DESKAGENT_HOME.
 *
 * @param {{ deskagentHome?: string, fileExists?: (p: string) => boolean, platform?: string }} opts
 * @returns {{ command: string, args: string[], kind: string } | null}
 */
function resolveVenvPython(opts = {}) {
  const { deskagentHome, fileExists, platform } = opts
  if (!deskagentHome || typeof fileExists !== 'function') return null

  const venvPython = venvPythonFor(deskagentHome, platform)
  const serverPy = path.join(deskagentHome, 'runner', 'server.py')

  if (fileExists(venvPython) && fileExists(serverPy)) {
    return { command: venvPython, args: [serverPy], kind: 'venv-python' }
  }
  return null
}

module.exports = { resolveVenvPython, venvPythonFor }
