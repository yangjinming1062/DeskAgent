// Launches the electron app with ELECTRON_RUN_AS_NODE removed from the env.
//
// VS Code's integrated terminal exports ELECTRON_RUN_AS_NODE=1 for its own
// node usage. A child `electron` process that inherits it runs as PLAIN node
// — `require('electron')` returns the binary path string, `app` is undefined,
// and main.cjs crashes at `app.setPath`/`app.isPackaged`. The electron CLI
// (node_modules/electron/cli.js) does not strip it, so `pnpm dev` from a VS
// Code terminal dies instantly. This wrapper deletes the var (truly unsets —
// an empty string still triggers node mode) before spawning electron.
'use strict'

delete process.env.ELECTRON_RUN_AS_NODE

const path = require('node:path')
const fs = require('node:fs')
const { spawn } = require('node:child_process')
const electronPath = require('electron') // resolves to the electron executable path

// Auto-wire the local Runner so dev mode gets local STT/TTS/tools without
// manually setting DESKAGENT_DESKTOP_PYTHON every session. Detects a runner
// venv in the source tree (../runner/.venv relative to this script's package
// root) and points the Desktop at it + the repo root for runner/server.py.
// Explicit env vars always win.
const repoRoot = path.resolve(__dirname, '..', '..')
const venvPython = process.platform === 'win32'
  ? path.join(repoRoot, 'runner', '.venv', 'Scripts', 'python.exe')
  : path.join(repoRoot, 'runner', '.venv', 'bin', 'python')
if (!process.env.DESKAGENT_DESKTOP_PYTHON && fs.existsSync(venvPython)) {
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
