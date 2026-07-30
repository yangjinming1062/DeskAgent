'use strict'

const path = require('node:path')
const fs = require('node:fs')
const yaml = require('yaml')

// Cap to keep a malicious or buggy renderer from streaming gigabytes through
// the IPC channel and into the runner's hot config path. Enforced in
// _commit so every write channel (write / patch / skills toggle) is
// covered.
const MAX_CONTENT_BYTES = 1_000_000

// Schema-evolution cleanup. Older runner configs carry keys that have no
// consumer anymore; we strip them on every write so the file converges
// after a single save. Each path is a key-path under the top-level YAML
// mapping (e.g. ['debug', 'browser'] → `debug.browser`).
const DEPRECATED_KEY_PATHS = [
  ['debug', 'browser'],
  ['debug', 'terminal'],
  ['terminal', 'default_backend']
]

function stripDeprecated(doc) {
  for (const p of DEPRECATED_KEY_PATHS) doc.deleteIn(p)
  return doc
}

// Module-level lock shared by every config-writer caller. Two IPC handlers
// (deskagent:runner-config:write / deskagent:skill:set-enabled) both target the same
// $DESKAGENT_HOME/config.yaml and both restart the Runner bridge afterward — without
// a shared lock, a fast double-toggle on the Skills page can race a runner-
// config save and produce a torn write or a bridge restart against a config
// the other writer already advanced past.
let inFlightWrite = null

/**
 * Patch one key-path of `$DESKAGENT_HOME/config.yaml` atomically and restart the
 * Runner bridge on success. The shared lock makes the (read → mutate → write
 * → restart) pipeline observable to any other config-writer caller on the
 * same process.
 *
 * The optional `mutate(doc)` callback runs **inside the lock** with a fresh
 * read of the file, so callers that need to read-modify-write one key can
 * delegate the whole pipeline here without leaking a stale read past the
 * restart point. The callback's return value is forwarded as `result.mutated`
 * in the success envelope — useful for callers that need the post-write
 * view to build a response (e.g. skills.cjs computes the new enabled set
 * from the just-committed disabled list).
 *
 * `stripDeprecated` is always applied after the mutation, so callers never
 * need to thread a transform.
 */
async function patchAndCommit({ deskagentHome, path: keyPath, value, deps, op = 'set', mutate }) {
  if (!Array.isArray(keyPath) || keyPath.length === 0) {
    return { ok: false, error: 'path must be a non-empty array' }
  }
  if (op !== 'set' && op !== 'delete') {
    return { ok: false, error: `unknown op: ${op}` }
  }

  const configPath = path.join(deskagentHome, 'config.yaml')

  return _runLocked(async () => {
    let current = ''
    try {
      current = await fs.promises.readFile(configPath, 'utf8')
    } catch (err) {
      if (err.code !== 'ENOENT') return { ok: false, error: err.message }
    }

    const doc = yaml.parseDocument(current)
    if (doc.errors.length > 0) {
      return { ok: false, error: doc.errors[0].message }
    }
    let mutated
    if (mutate) {
      try {
        mutated = mutate(doc)
      } catch (err) {
        return { ok: false, error: err?.message || String(err) }
      }
    } else if (op === 'delete') {
      doc.deleteIn(keyPath)
    } else {
      doc.setIn(keyPath, value)
    }
    stripDeprecated(doc)
    return _commit(doc.toString(), { configPath, deps }, { mutated })
  })
}

/**
 * Commit a fully-rendered config string atomically and restart the Runner
 * bridge on success. Same shared lock as patchAndCommit — caller is
 * responsible for producing the string (e.g. the renderer round-tripped the
 * doc, or we just stripped deprecated keys from an incoming payload).
 */
async function commitRaw(content, { configPath, deps }) {
  return _runLocked(() => _commit(content, { configPath, deps }))
}

async function _runLocked(task) {
  // Wait for any in-flight write to finish before starting a new one.
  // This serialises concurrent IPC calls (e.g. rapid skill toggles)
  // instead of rejecting the second call with an error.
  while (inFlightWrite) {
    await inFlightWrite.catch(() => {})
  }
  inFlightWrite = task()
  try {
    return await inFlightWrite
  } catch (err) {
    return { ok: false, error: err?.message || String(err) }
  } finally {
    inFlightWrite = null
  }
}

async function _commit(content, { configPath, deps }, { mutated } = {}) {
  if (Buffer.byteLength(content, 'utf8') > MAX_CONTENT_BYTES) {
    return { ok: false, error: `config content exceeds ${MAX_CONTENT_BYTES} bytes` }
  }

  const { atomicWriteFile, restartRunnerBridge, rememberLog, onCommit } = deps

  await atomicWriteFile(configPath, content)
  onCommit?.()
  const restartResult = await restartRunnerBridge()
  if (!restartResult?.ok && !restartResult?.noop) {
    const message = restartResult.error || restartResult.reason || 'restart failed'
    rememberLog?.(`[config-writer] save succeeded but restart failed: ${message}`)
    return { ok: true, restarted: false, restartError: message, mutated }
  }
  return { ok: true, restarted: true, mutated }
}

module.exports = { patchAndCommit, commitRaw, stripDeprecated, MAX_CONTENT_BYTES, DEPRECATED_KEY_PATHS }
