'use strict'

const path = require('node:path')

/**
 * Path to the python executable inside the venv at `$ZAST_HOME/runner/.venv`.
 * Cross-platform: `Scripts/python.exe` on Windows, `bin/python` elsewhere.
 *
 * @param {string} zastHome
 * @param {string} [platform] — defaults to `process.platform`; pass `process.platform` from callers
 *   for testability.
 */
function venvPythonFor(zastHome, platform = process.platform) {
  return platform === 'win32'
    ? path.join(zastHome, 'runner', '.venv', 'Scripts', 'python.exe')
    : path.join(zastHome, 'runner', '.venv', 'bin', 'python')
}

/**
 * Resolve the runner's venv Python + server.py from $ZAST_HOME.
 *
 * @param {{ zastHome?: string, fileExists?: (p: string) => boolean, platform?: string }} opts
 * @returns {{ command: string, args: string[], kind: string } | null}
 */
function resolveVenvPython(opts = {}) {
  const { zastHome, fileExists, platform } = opts
  if (!zastHome || typeof fileExists !== 'function') return null

  const venvPython = venvPythonFor(zastHome, platform)
  const serverPy = path.join(zastHome, 'runner', 'server.py')

  if (fileExists(venvPython) && fileExists(serverPy)) {
    return { command: venvPython, args: [serverPy], kind: 'venv-python' }
  }
  return null
}

module.exports = { resolveVenvPython, venvPythonFor }
