'use strict'

const path = require('node:path')
const os = require('node:os')

function deskagentHome() {
  if (process.platform === 'win32') {
    const local = process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local')
    return path.join(local, 'DeskAgent')
  }
  if (process.platform === 'darwin') {
    return path.join(os.homedir(), 'Library', 'Application Support', 'DeskAgent')
  }
  return path.join(os.homedir(), '.deskagent')
}

module.exports = { deskagentHome }
