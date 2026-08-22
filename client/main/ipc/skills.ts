import path from 'node:path'

import { IPC } from '@ipc/contracts'
import type { IpcMain } from 'electron'

import * as store from '../shared/lib/runner-config-store'
import { buildSkillSummaries } from '../shared/lib/skill-index'
import { buildToolsetRoster } from '../shared/lib/toolset-index'

export interface SkillsIpcDeps {
  spiritagentHome?: null | string
  getRunnerBridge?: () => { getTools?: () => Record<string, unknown>[] } | null | undefined
  ipcMain: IpcMain
}

export function registerSkillsIpc({ spiritagentHome, getRunnerBridge, ipcMain }: SkillsIpcDeps): void {
  const skillsRoot = path.join(spiritagentHome || '', 'skills')

  ipcMain.handle(IPC.invoke.skillsList, () => ({
    ok: true,
    skills: buildSkillSummaries(skillsRoot, store.getDisabledSet())
  }))

  ipcMain.handle(IPC.invoke.skillSetEnabled, async (_evt, payload) => {
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
        const skills = (config.skills as { disabled?: unknown } | undefined) ?? {}
        const list = Array.isArray(skills.disabled) ? skills.disabled : []
        const next = new Set(list.map(String))

        if (enabled) {
          next.delete(name)
        } else {
          next.add(name)
        }

        config.skills = { ...skills, disabled: [...next].sort() }

        return next
      })

      if (!result.ok) {
        return result
      }

      return { ok: true, skills: buildSkillSummaries(skillsRoot, (result.mutated as Set<string>) ?? new Set<string>()) }
    }

    return { ok: true, skills: buildSkillSummaries(skillsRoot, store.getDisabledSet()) }
  })

  ipcMain.handle(IPC.invoke.toolsetsList, () => {
    let schemas: Record<string, unknown>[] = []

    try {
      schemas = (getRunnerBridge?.()?.getTools?.() as Record<string, unknown>[]) ?? []
    } catch {
      schemas = []
    }

    const disabled = store.getDisabledSet('toolsets')

    return { ok: true, toolsets: buildToolsetRoster(schemas, disabled) }
  })

  ipcMain.handle(IPC.invoke.toolsetSetEnabled, async (_evt, payload) => {
    const { enabled, id } = payload ?? {}

    if (typeof id !== 'string' || !id) {
      return { error: 'invalid id', ok: false }
    }

    if (typeof enabled !== 'boolean') {
      return { error: 'invalid enabled', ok: false }
    }

    let preWriteSchemas: Record<string, unknown>[] = []

    try {
      preWriteSchemas = (getRunnerBridge?.()?.getTools?.() as Record<string, unknown>[]) ?? []
    } catch {
      preWriteSchemas = []
    }

    const current = store.getDisabledSet('toolsets')
    const wasDisabled = current.has(id)

    if (wasDisabled !== enabled) {
      const result = await store.mutate(config => {
        const toolsets = (config.toolsets as { disabled?: unknown } | undefined) ?? {}
        const list = Array.isArray(toolsets.disabled) ? toolsets.disabled : []
        const next = new Set(list.map(String))

        if (enabled) {
          next.delete(id)
        } else {
          next.add(id)
        }

        config.toolsets = { ...toolsets, disabled: [...next].sort() }

        return next
      })

      if (!result.ok) {
        return result
      }
    }

    return { ok: true, toolsets: buildToolsetRoster(preWriteSchemas, store.getDisabledSet('toolsets')) }
  })
}
