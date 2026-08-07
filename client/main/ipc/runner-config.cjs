'use strict'

const path = require('node:path')
const fs = require('node:fs')
const yaml = require('yaml')

const { atomicWriteFile } = require('../shared/utils.cjs')
const { patchAndCommit, commitRaw, MAX_CONTENT_BYTES } = require('../shared/lib/config-writer.cjs')
const { invalidateDisabledCache } = require('../shared/lib/skill-index.cjs')

function registerRunnerConfigIpc({ ipcMain, deps }) {
  const { deskagentHome, restartRunnerBridge, rememberLog } = deps
  const configPath = path.join(deskagentHome, 'config.yaml')

  // onCommit drops the readDisabledSet mtime cache after any successful
  // write so a same-tick patch on `skills.disabled` doesn't return a stale
  // set on the next read.
  const writeDeps = { atomicWriteFile, restartRunnerBridge, rememberLog, onCommit: invalidateDisabledCache }

  ipcMain.handle('deskagent:runner-config:read', async () => {
    try {
      const content = await fs.promises.readFile(configPath, 'utf8')
      return { ok: true, content }
    } catch (error) {
      // ENOENT is the legitimate "no config yet" path (first run / clean
      // install) — surface as empty content so the renderer seeds defaults.
      if (error.code === 'ENOENT') {
        return { ok: true, content: '' }
      }
      return { ok: false, error: error.message }
    }
  })

  ipcMain.handle('deskagent:runner-config:write', async (_event, newContent) => {
    if (typeof newContent !== 'string') {
      return { ok: false, error: 'config content must be a string' }
    }

    // Anti-DoS cap: a malicious or buggy renderer can otherwise hand us a
    // multi-hundred-MB string and force yaml.parseDocument to build the full
    // AST before _commit's check ever fires. The patch channel is unaffected
    // — it reads the small on-disk file inside patchAndCommit.
    if (Buffer.byteLength(newContent, 'utf8') > MAX_CONTENT_BYTES) {
      return { ok: false, error: `config content exceeds ${MAX_CONTENT_BYTES} bytes` }
    }

    // parseDocument (not yaml.parse) so we can inspect .errors instead of
    // relying on throw; also reject non-mapping top-level payloads (scalars,
    // arrays) which yaml.parse happily accepts but the runner can't.
    const doc = yaml.parseDocument(newContent)
    if (doc.errors.length > 0) {
      return { ok: false, error: doc.errors[0].message }
    }
    if (!doc.contents || typeof doc.contents !== 'object' || Array.isArray(doc.contents)) {
      return { ok: false, error: 'config root must be a YAML mapping' }
    }

    return commitRaw(doc.toString(), {
      configPath,
      deps: writeDeps
    })
  })

  // Surgical update for pages that don't need to round-trip the entire
  // document (e.g. the MCP settings page just edits the mcp_servers
  // section). The lock + atomic write + bridge restart + deprecated-key
  // strip live in lib/config-writer.cjs so skills.cjs can share them.
  ipcMain.handle('deskagent:runner-config:patch', async (_event, patch) => {
    if (!patch || !Array.isArray(patch.path) || patch.path.length === 0) {
      return { ok: false, error: 'patch.path must be a non-empty array' }
    }
    const op = patch.op ?? 'set'
    if (op !== 'set' && op !== 'delete') {
      return { ok: false, error: `unknown op: ${op}` }
    }
    return patchAndCommit({
      deskagentHome,
      path: patch.path,
      value: patch.value,
      deps: writeDeps,
      mutate: doc => {
        if (op === 'delete') doc.deleteIn(patch.path)
        else doc.setIn(patch.path, patch.value)
      }
    })
  })
}

module.exports = { registerRunnerConfigIpc }
