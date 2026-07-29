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

const { spawn } = require('node:child_process')
const electronPath = require('electron') // resolves to the electron executable path

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
