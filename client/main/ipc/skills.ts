import path from 'node:path'

import type { IpcMain } from 'electron'

import * as store from '../shared/lib/runner-config-store'
import { buildSkillSummaries } from '../shared/lib/skill-index'
import { buildToolsetRoster } from '../shared/lib/toolset-index'

export interface SkillsIpcDeps {
  deskagentHome?: null | string
  ipcMain: IpcMain
  runnerBridge?: any
}

export function registerSkillsIpc({ deskagentHome, ipcMain, runnerBridge }: SkillsIpcDeps): void {
  const skillsRoot = path.join(deskagentHome || '', 'skills')

  ipcMain.handle('deskagent:skills:list', () => ({
    ok: true,
    skills: buildSkillSummaries(skillsRoot, store.getDisabledSet())
  }))

  ipcMain.handle('deskagent:skill:set-enabled', async (_evt, payload) => {
    const { enabled, name } = payload ?? {}

    if (typeof name !== 'string' || !name) {
      return { error: 'invalid name', ok: false }
    }

    if (typeof enabled !== 'boolean') {
      return { error: 'invalid enabled', ok: false }
    }

    if (enabled) {
      const summary = buildSkillSummaries(skillsRoot, store.getDisabledSet()).find(s => s.name === name)

      if (!summary || !summary.compatible) {
        return { error: 'Skill is not available on the current platform.', ok: false }
      }
    }

    const current = store.getDisabledSet()
    const wasDisabled = current.has(name)

    if (wasDisabled !== enabled) {
      const result = await store.mutate(config => {
        const list = Array.isArray(config?.skills?.disabled) ? config.skills.disabled : []
        const next = new Set(list.map(String))

        if (enabled) {
          next.delete(name)
        } else {
          next.add(name)
        }

        if (!config.skills) {
          config.skills = {}
        }

        config.skills.disabled = [...next].sort()

        return next
      })

      if (!result.ok) {
        return result
      }

      return { ok: true, skills: buildSkillSummaries(skillsRoot, (result.mutated as Set<string>) ?? new Set<string>()) }
    }

    return { ok: true, skills: buildSkillSummaries(skillsRoot, store.getDisabledSet()) }
  })

  ipcMain.handle('deskagent:toolsets:list', () => {
    let schemas: any[] = []

    try {
      schemas = runnerBridge?.getTools?.() ?? []
    } catch {
      schemas = []
    }

    const disabled = store.getDisabledSet('toolsets')

    return { ok: true, toolsets: buildToolsetRoster(schemas, disabled) }
  })

  ipcMain.handle('deskagent:toolset:set-enabled', async (_evt, payload) => {
    const { enabled, id } = payload ?? {}

    if (typeof id !== 'string' || !id) {
      return { error: 'invalid id', ok: false }
    }

    if (typeof enabled !== 'boolean') {
      return { error: 'invalid enabled', ok: false }
    }

    let preWriteSchemas: any[] = []

    try {
      preWriteSchemas = runnerBridge?.getTools?.() ?? []
    } catch {
      preWriteSchemas = []
    }

    const current = store.getDisabledSet('toolsets')
    const wasDisabled = current.has(id)

    if (wasDisabled !== enabled) {
      const result = await store.mutate(config => {
        const list = Array.isArray(config?.toolsets?.disabled) ? config.toolsets.disabled : []
        const next = new Set(list.map(String))

        if (enabled) {
          next.delete(id)
        } else {
          next.add(id)
        }

        if (!config.toolsets) {
          config.toolsets = {}
        }

        config.toolsets.disabled = [...next].sort()

        return next
      })

      if (!result.ok) {
        return result
      }
    }

    return { ok: true, toolsets: buildToolsetRoster(preWriteSchemas, store.getDisabledSet('toolsets')) }
  })
}
