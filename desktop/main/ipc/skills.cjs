const path = require('node:path')

const {
  readDisabledSet,
  buildSkillSummaries,
  invalidateDisabledCache
} = require('../shared/lib/skill-index.cjs')
const {
  readDisabledToolsets,
  invalidateDisabledToolsetsCache,
  buildToolsetRoster
} = require('../shared/lib/toolset-index.cjs')
const { patchAndCommit } = require('../shared/lib/config-writer.cjs')

function registerSkillsIpc({ ipcMain, deps, deskagentHome }) {
  // deps must expose atomicWriteFile + restartRunnerBridge (passed through
  // to lib/config-writer.cjs so the in-flight write lock is shared with
  // deskagent:runner-config:write and deskagent:runner-config:patch).
  // onCommit invalidates the readDisabledSet mtime cache after a successful
  // atomic write so the next read after a same-mtime-tick write doesn't
  // return the pre-write set.
  const writeDeps = { ...deps, onCommit: invalidateDisabledCache }
  const toolsetsWriteDeps = { ...deps, onCommit: invalidateDisabledToolsetsCache }
  const skillsRoot = path.join(deskagentHome, 'skills')
  const configPath = path.join(deskagentHome, 'config.yaml')

  ipcMain.handle('deskagent:skills:list', () => ({
    ok: true,
    skills: buildSkillSummaries(skillsRoot, readDisabledSet(configPath))
  }))

  // The read-modify-write cycle on $DESKAGENT_HOME/config.yaml must sit inside
  // the shared write lock — otherwise two fast toggles both read the same
  // starting state and the second write clobbers the first. We hand
  // patchAndCommit a `mutate` callback that re-reads the doc under the lock,
  // flips one entry, and writes it back. The lock guarantees that mutate
  // runs sequentially across every concurrent IPC call.
  //
  // A no-op toggle (state already matches) skips the write+restart entirely;
  // the bridge cold start otherwise costs 1-3s of dead tool time per click.
  ipcMain.handle('deskagent:skill:set-enabled', async (_evt, payload) => {
    const { name, enabled } = payload ?? {}
    if (typeof name !== 'string' || !name) return { ok: false, error: 'invalid name' }
    if (typeof enabled !== 'boolean') return { ok: false, error: 'invalid enabled' }

    // Defense in depth: renderer filters incompatible skills already, so this
    // only catches programmatic or out-of-date callers. `!summary` covers
    // the case where the skill was removed between load and toggle.
    if (enabled) {
      const summary = buildSkillSummaries(skillsRoot, readDisabledSet(configPath)).find(s => s.name === name)
      if (!summary || !summary.compatible) {
        return { ok: false, error: 'Skill is not available on the current platform.' }
      }
    }

    const current = readDisabledSet(configPath)
    const wasDisabled = current.has(name)
    if (wasDisabled !== enabled) {
      const result = await patchAndCommit({
        deskagentHome,
        path: ['skills', 'disabled'],
        deps: writeDeps,
        mutate: doc => {
          const list = Array.isArray(doc.getIn(['skills', 'disabled'])?.toJSON?.())
            ? doc.getIn(['skills', 'disabled']).toJSON()
            : []
          const next = new Set(list.map(String))
          if (enabled) next.delete(name)
          else next.add(name)
          doc.setIn(['skills', 'disabled'], [...next].sort())
          return next
        }
      })
      if (!result.ok) return result
      return { ok: true, skills: buildSkillSummaries(skillsRoot, result.mutated) }
    }

    return { ok: true, skills: buildSkillSummaries(skillsRoot, readDisabledSet(configPath)) }
  })

  // Toolset roster uses the cached Runner tool schemas (returned by
  // `runnerBridge.getTools()`; `[]` when Runner isn't ready). The disabled
  // toolset ids are mtime-cached from `toolsets.disabled` in config.yaml.
  // The renderer cross-references each rosterset's id with the static
  // `desktop/src/lib/toolset-catalog.ts` for label/icon/description.
  ipcMain.handle('deskagent:toolsets:list', () => {
    let schemas = []
    try {
      schemas = deps.runnerBridge?.getTools?.() ?? []
    } catch {
      schemas = []
    }
    const disabled = readDisabledToolsets(configPath)

    return { ok: true, toolsets: buildToolsetRoster(schemas, disabled) }
  })

  // Symmetric with `deskagent:skill:set-enabled` — writes to `toolsets.disabled`
  // under the shared write lock, returns the post-write roster. Toggling a
  // toolset triggers `restartRunnerBridge()` so the Runner re-reads
  // `$DESKAGENT_HOME/config.yaml` and rebuilds its filtered schema set.
  //
  // Capture the cached tool schemas BEFORE patchAndCommit: the bridge clears
  // its `cachedTools` on stop, and `_fetchAndCacheTools()` re-populates it
  // asynchronously after `runner_ready`. Re-reading `runnerBridge.getTools()`
  // after `restartRunnerBridge()` returns would race the in-flight refetch and
  // emit a roster with empty `toolNames` for every entry. The pre-write
  // snapshot is one bridge cycle behind the new Runner, but it is non-empty,
  // so the renderer's chip strip never goes blank mid-toggle.
  ipcMain.handle('deskagent:toolset:set-enabled', async (_evt, payload) => {
    const { id, enabled } = payload ?? {}
    if (typeof id !== 'string' || !id) return { ok: false, error: 'invalid id' }
    if (typeof enabled !== 'boolean') return { ok: false, error: 'invalid enabled' }

    let preWriteSchemas = []
    try {
      preWriteSchemas = deps.runnerBridge?.getTools?.() ?? []
    } catch {
      preWriteSchemas = []
    }

    const current = readDisabledToolsets(configPath)
    const wasDisabled = current.has(id)
    if (wasDisabled !== enabled) {
      const result = await patchAndCommit({
        deskagentHome,
        path: ['toolsets', 'disabled'],
        deps: toolsetsWriteDeps,
        mutate: doc => {
          const list = Array.isArray(doc.getIn(['toolsets', 'disabled'])?.toJSON?.())
            ? doc.getIn(['toolsets', 'disabled']).toJSON()
            : []
          const next = new Set(list.map(String))
          if (enabled) next.delete(id)
          else next.add(id)
          doc.setIn(['toolsets', 'disabled'], [...next].sort())
          return next
        }
      })
      if (!result.ok) return result
    }

    return { ok: true, toolsets: buildToolsetRoster(preWriteSchemas, readDisabledToolsets(configPath)) }
  })
}

module.exports = { registerSkillsIpc }
