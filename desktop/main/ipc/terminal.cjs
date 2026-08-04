'use strict'

const path = require('node:path')
const fs = require('node:fs')
const crypto = require('node:crypto')
const { app } = require('electron')

// Interactive shell session over node-pty. Renderer holds session id
// and listens on deskagent:terminal:<id>:{data,exit} for output.
function terminalShellCommand() {
  if (process.platform === 'win32') {
    return { args: [], command: process.env.COMSPEC || 'cmd.exe' }
  }

  const configuredShell = process.env.SHELL || ''
  const shellPath =
    (path.isAbsolute(configuredShell) && fs.existsSync(configuredShell) && configuredShell) ||
    ['/bin/zsh', '/bin/bash', '/bin/sh'].find(candidate => fs.existsSync(candidate)) ||
    '/bin/sh'
  const shellName = path.basename(shellPath)
  const interactiveArgs = shellName.includes('zsh') || shellName.includes('bash') ? ['-il'] : ['-i']

  return { args: interactiveArgs, command: shellPath, name: shellName }
}

function safeTerminalCwd(cwd) {
  const candidate = path.resolve(String(cwd || app.getPath('home')))

  try {
    const stat = fs.statSync(candidate)

    return stat.isDirectory() ? candidate : path.dirname(candidate)
  } catch {
    return app.getPath('home')
  }
}

function terminalShellEnv() {
  const env = { ...process.env }

  // Strip npm prefix to avoid nvm/proto warnings in interactive shell.
  for (const key of Object.keys(env)) {
    if (key === 'npm_config_prefix' || key.startsWith('npm_config_') || key.startsWith('npm_package_')) {
      delete env[key]
    }
  }

  // Strip color/theme vars from non-tty agent shell; force truecolor for our PTY.
  delete env.NO_COLOR
  delete env.FORCE_COLOR
  delete env.COLORFGBG

  env.COLORTERM = 'truecolor'
  env.LC_CTYPE = env.LC_CTYPE || 'UTF-8'
  env.TERM = 'xterm-256color'
  env.TERM_PROGRAM = 'DeskAgent'
  env.TERM_PROGRAM_VERSION = app.getVersion()

  return env
}

function terminalChannel(id, suffix) {
  return `deskagent:terminal:${id}:${suffix}`
}

function disposeTerminalSession(terminalSessions, id) {
  const sessionInfo = terminalSessions.get(id)

  if (!sessionInfo) {
    return false
  }

  terminalSessions.delete(id)

  try {
    sessionInfo.pty.kill()
  } catch {
    // Process may already be gone.
  }

  return true
}

function registerTerminalIpc({ ipcMain, nodePty, terminalSessions }) {
  ipcMain.handle('deskagent:terminal:start', async (event, payload = {}) => {
    if (!nodePty) {
      throw new Error('PTY support is unavailable. Reinstall desktop dependencies and restart DeskAgent.')
    }

    const id = crypto.randomUUID()
    const { args, command, name } = terminalShellCommand()
    const cwd = safeTerminalCwd(payload?.cwd)
    const cols = Math.max(2, Number.parseInt(String(payload?.cols || 80), 10) || 80)
    const rows = Math.max(2, Number.parseInt(String(payload?.rows || 24), 10) || 24)
    const ptyProcess = nodePty.spawn(command, args, {
      cols,
      cwd,
      env: terminalShellEnv(),
      name: 'xterm-256color',
      rows
    })

    terminalSessions.set(id, { pty: ptyProcess })

    const send = (suffix, payload) => {
      if (event.sender.isDestroyed()) {
        return
      }

      event.sender.send(terminalChannel(id, suffix), payload)
    }

    ptyProcess.onData(data => send('data', data))
    ptyProcess.onExit(({ exitCode, signal }) => {
      terminalSessions.delete(id)
      send('exit', { code: exitCode, signal: signal || null })
    })
    event.sender.once('destroyed', () => disposeTerminalSession(terminalSessions, id))

    return { cwd, id, shell: name }
  })

  ipcMain.handle('deskagent:terminal:write', (_event, id, data) => {
    const sessionInfo = terminalSessions.get(String(id || ''))

    if (!sessionInfo) {
      return false
    }

    sessionInfo.pty.write(String(data || ''))

    return true
  })

  ipcMain.handle('deskagent:terminal:resize', (_event, id, size = {}) => {
    const sessionInfo = terminalSessions.get(String(id || ''))

    if (!sessionInfo) {
      return false
    }

    const cols = Math.max(2, Number.parseInt(String(size?.cols || 80), 10) || 80)
    const rows = Math.max(2, Number.parseInt(String(size?.rows || 24), 10) || 24)

    sessionInfo.pty.resize(cols, rows)

    return true
  })

  ipcMain.handle('deskagent:terminal:dispose', (_event, id) =>
    disposeTerminalSession(terminalSessions, String(id || ''))
  )
}

module.exports = { registerTerminalIpc, disposeTerminalSession, terminalChannel }
