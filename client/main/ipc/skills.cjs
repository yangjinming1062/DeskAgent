const path = require('node:path')

const { buildSkillSummaries } = require('../shared/lib/skill-index.cjs')
const { buildToolsetRoster } = require('../shared/lib/toolset-index.cjs')
const store = require('../shared/lib/runner-config-store.cjs')

function registerSkillsIpc({ ipcMain, deps, deskagentHome }) {
  const skillsRoot = path.join(deskagentHome, 'skills')

  ipcMain.handle('deskagent:skills:list', () => ({
    ok: true,
    skills: buildSkillSummaries(skillsRoot, store.getDisabledSet())
  }))

  // A no-op toggle (state already matches) skips the write + push entirely;
  // the config push is cheap but there's no reason to fire it for a no-op.
  ipcMain.handle('deskagent:skill:set-enabled', async (_evt, payload) => {
    const { name, enabled } = payload ?? {}
    if (typeof name !== 'string' || !name) return { ok: false, error: 'invalid name' }
    if (typeof enabled !== 'boolean') return { ok: false, error: 'invalid enabled' }

    // Defense in depth: renderer filters incompatible skills already, so this
    // only catches programmatic or out-of-date callers. `!summary` covers
    // the case where the skill was removed between load and toggle.
    if (enabled) {
      const summary = buildSkillSummaries(skillsRoot, store.getDisabledSet()).find(s => s.name === name)
      if (!summary || !summary.compatible) {
        return { ok: false, error: 'Skill is not available on the current platform.' }
      }
    }

    const current = store.getDisabledSet()
    const wasDisabled = current.has(name)
    if (wasDisabled !== enabled) {
      const result = await store.mutate(config => {
        const list = Array.isArray(config?.skills?.disabled) ? config.skills.disabled : []
        const next = new Set(list.map(String))
        if (enabled) next.delete(name)
        else next.add(name)
        if (!config.skills) config.skills = {}
        config.skills.disabled = [...next].sort()
        return next
      })
      if (!result.ok) return result
      return { ok: true, skills: buildSkillSummaries(skillsRoot, result.mutated) }
    }

    return { ok: true, skills: buildSkillSummaries(skillsRoot, store.getDisabledSet()) }
  })

  // Toolset roster uses the cached Runner tool schemas (returned by
  // `runnerBridge.getTools()`; `[]` when Runner isn't ready).
  // The renderer cross-references each rosterset's id with the static
  // `client/renderer/shared/lib/toolset-catalog.ts` for label/icon/description.
  ipcMain.handle('deskagent:toolsets:list', () => {
    let schemas = []
    try {
      schemas = deps.runnerBridge?.getTools?.() ?? []
    } catch {
      schemas = []
    }
    const disabled = store.getDisabledSet('toolsets')

    return { ok: true, toolsets: buildToolsetRoster(schemas, disabled) }
  })

  // Snapshot schemas BEFORE the write — the push triggers an async tools_changed
  // refetch; reading getTools() after would race it and blank the renderer's chip strip.
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

    const current = store.getDisabledSet('toolsets')
    const wasDisabled = current.has(id)
    if (wasDisabled !== enabled) {
      const result = await store.mutate(config => {
        const list = Array.isArray(config?.toolsets?.disabled) ? config.toolsets.disabled : []
        const next = new Set(list.map(String))
        if (enabled) next.delete(id)
        else next.add(id)
        if (!config.toolsets) config.toolsets = {}
        config.toolsets.disabled = [...next].sort()
        return next
      })
      if (!result.ok) return result
    }

    return { ok: true, toolsets: buildToolsetRoster(preWriteSchemas, store.getDisabledSet('toolsets')) }
  })
}

module.exports = { registerSkillsIpc }
