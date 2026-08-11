'use strict'

const store = require('../shared/lib/runner-config-store.cjs')

function registerRunnerConfigIpc({ ipcMain }) {
  ipcMain.handle('deskagent:runner-config:read', async () => {
    try {
      const content = JSON.stringify(store.read(), null, 2)
      return { ok: true, content }
    } catch (error) {
      return { ok: false, error: error.message }
    }
  })

  ipcMain.handle('deskagent:runner-config:write', async (_event, newContent) => {
    if (typeof newContent !== 'string') {
      return { ok: false, error: 'config content must be a string' }
    }

    let obj
    try {
      obj = JSON.parse(newContent)
    } catch (error) {
      return { ok: false, error: error.message }
    }
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) {
      return { ok: false, error: 'config root must be a JSON object' }
    }

    return store.write(obj)
  })

  ipcMain.handle('deskagent:runner-config:patch', async (_event, patch) => {
    if (!patch || !Array.isArray(patch.path) || patch.path.length === 0) {
      return { ok: false, error: 'patch.path must be a non-empty array' }
    }
    const op = patch.op ?? 'set'
    if (op !== 'set' && op !== 'delete') {
      return { ok: false, error: `unknown op: ${op}` }
    }
    return store.patch(patch.path, { value: patch.value, op })
  })
}

module.exports = { registerRunnerConfigIpc }
