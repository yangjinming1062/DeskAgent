'use strict'

const yaml = require('yaml')

const store = require('../shared/lib/runner-config-store.cjs')

function registerRunnerConfigIpc({ ipcMain }) {
  ipcMain.handle('deskagent:runner-config:read', async () => {
    try {
      const content = yaml.stringify(store.read())
      return { ok: true, content }
    } catch (error) {
      return { ok: false, error: error.message }
    }
  })

  ipcMain.handle('deskagent:runner-config:write', async (_event, newContent) => {
    if (typeof newContent !== 'string') {
      return { ok: false, error: 'config content must be a string' }
    }

    // parseDocument (not yaml.parse) to inspect .errors and reject non-mapping roots.
    const doc = yaml.parseDocument(newContent)
    if (doc.errors.length > 0) {
      return { ok: false, error: doc.errors[0].message }
    }
    if (!doc.contents || typeof doc.contents !== 'object' || Array.isArray(doc.contents)) {
      return { ok: false, error: 'config root must be a YAML mapping' }
    }

    const obj = doc.toJS()
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
