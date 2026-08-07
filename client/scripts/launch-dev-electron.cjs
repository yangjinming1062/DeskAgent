'use strict'

const path = require('node:path')
const fs = require('node:fs')
const { spawn } = require('node:child_process')
const electronPath = require('electron')

delete process.env.ELECTRON_RUN_AS_NODE

// Auto-wire the local Runner so dev mode gets local STT/TTS/tools without
// manually setting DESKAGENT_DESKTOP_PYTHON every session. Detects a runner
// venv in the source tree (../runner/.venv relative to this script's package
// root) and points the Desktop at it + the repo root for runner/server.py.
// The venv must have the audio stack installed (faster_whisper / piper /
// pyttsx3) — checked via their package marker files instead of spawning the
// interpreter, since importing faster_whisper alone takes seconds and would
// block every dev launch.
// Explicit env vars always win.
const repoRoot = path.resolve(__dirname, '..', '..')
const venvRoot = path.join(repoRoot, 'runner', '.venv')
const venvPython =
  process.platform === 'win32' ? path.join(venvRoot, 'Scripts', 'python.exe') : path.join(venvRoot, 'bin', 'python')

function findSitePackages(venvRoot) {
  if (process.platform === 'win32') {
    return path.join(venvRoot, 'Lib', 'site-packages')
  }
  try {
    const entries = fs.readdirSync(path.join(venvRoot, 'lib'))
    const pyDir = entries.find(e => /^python3\.\d+$/.test(e))
    return pyDir ? path.join(venvRoot, 'lib', pyDir, 'site-packages') : ''
  } catch {
    return ''
  }
}

function venvHasAudioStack() {
  const sitePackages = findSitePackages(venvRoot)
  if (!sitePackages) {
    return false
  }
  for (const pkg of ['faster_whisper', 'piper', 'pyttsx3']) {
    if (!fs.existsSync(path.join(sitePackages, pkg, '__init__.py'))) {
      return false
    }
  }
  return true
}

if (!process.env.DESKAGENT_DESKTOP_PYTHON && fs.existsSync(venvPython) && venvHasAudioStack()) {
  process.env.DESKAGENT_DESKTOP_PYTHON = venvPython
}
if (!process.env.DESKAGENT_DESKTOP_RUNNER_REPO_ROOT && fs.existsSync(path.join(repoRoot, 'runner', 'server.py'))) {
  process.env.DESKAGENT_DESKTOP_RUNNER_REPO_ROOT = repoRoot
}

const child = spawn(electronPath, ['.'], {
  stdio: 'inherit',
  env: process.env,
  shell: true
})

child.on('error', err => {
  console.error('[launch-dev-electron] failed to spawn electron:', err)
  process.exit(1)
})
child.on('exit', (code, signal) => {
  process.exit(code ?? (signal ? 128 : 0))
})
