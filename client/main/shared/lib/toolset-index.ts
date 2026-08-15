import type { ToolsetItem } from '../ipc-contracts'

export const EXCLUDED_PREFIXES = ['mcp_']
export const EXCLUDED_PREFIX_RE = new RegExp(`^(?:${EXCLUDED_PREFIXES.join('|')})`)

export interface ToolsetDef {
  extraTools?: string[]
  id: string
  prefixes?: string[]
}

// Source of truth catalog for the filter side. id matches
// ToolsetCatalogEntry.id in toolset-catalog.ts.
export const TOOLSET_DEFS: ToolsetDef[] = [
  { id: 'browser_automation', prefixes: ['browser_'] },
  { extraTools: ['read_file', 'write_file', 'patch', 'list_directory', 'search_files'], id: 'file_operations' },
  { extraTools: ['terminal'], id: 'terminal' },
  { extraTools: ['execute_code'], id: 'code_execution' },
  { extraTools: ['process'], id: 'process_management' },
  { extraTools: ['skills_list', 'skill_view', 'skill_manage'], id: 'skills_system' },
  { extraTools: ['memory_retain', 'memory_recall', 'memory_forget'], id: 'memory' },
  { extraTools: ['web_search', 'web_extract', 'search_tools'], id: 'web_tools' },
  { extraTools: ['image_generate'], id: 'image_generation' },
  { extraTools: ['text_to_speech_tool'], id: 'text_to_speech' },
  { extraTools: ['send_message_tool'], id: 'messaging' },
  { extraTools: ['cronjob'], id: 'scheduled_tasks' },
  { extraTools: ['agent_delegate_tool'], id: 'agent_delegation' },
  { extraTools: ['computer_use'], id: 'computer_use' },
  { extraTools: ['vision_analyze'], id: 'media_analysis' }
]

export function toolNamesForToolset(def: ToolsetDef, availableNames: Set<string>): string[] {
  const names: string[] = []
  const seen = new Set<string>()

  if (def.prefixes) {
    for (const prefix of def.prefixes) {
      for (const name of availableNames) {
        if (EXCLUDED_PREFIX_RE.test(name)) {
          continue
        }

        if (name.startsWith(prefix) && !seen.has(name)) {
          names.push(name)
          seen.add(name)
        }
      }
    }
  }

  if (def.extraTools) {
    for (const name of def.extraTools) {
      if (EXCLUDED_PREFIX_RE.test(name)) {
        continue
      }

      if (seen.has(name)) {
        continue
      }

      if (availableNames.has(name)) {
        names.push(name)
        seen.add(name)
      }
    }
  }

  return names
}

// Build the renderer-facing toolset roster. Used by `deskagent:toolsets:list`.
export function buildToolsetRoster(schemas: Array<{ name?: string }>, disabledToolsetIds: Set<string>): ToolsetItem[] {
  const availableNames = new Set(schemas.map(s => s?.name).filter(Boolean) as string[])

  return TOOLSET_DEFS.map(def => ({
    enabled: !disabledToolsetIds.has(def.id),
    id: def.id,
    toolNames: toolNamesForToolset(def, availableNames)
  }))
}
