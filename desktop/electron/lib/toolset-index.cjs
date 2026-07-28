// Mirror of the catalog shape in `desktop/src/lib/toolset-catalog.ts` and
// `runner/tools/toolsets/catalog.py`. Only `id`, `prefixes`, `extraTools` are
// needed here — label / description / icon live in the renderer-side catalog.
// Keep the three files in sync; each lists this invariant at the top.
//
// MCP tools (`mcp_*`) are explicitly excluded at runtime via
// `EXCLUDED_PREFIXES` regardless of catalog entry, as a defense-in-depth
// against future catalog drift.

const { readDisabledSet, invalidateDisabledCache } = require('./skill-index.cjs')

const EXCLUDED_PREFIXES = ['mcp_']
const EXCLUDED_PREFIX_RE = new RegExp(`^(?:${EXCLUDED_PREFIXES.join('|')})`)

// Source of truth catalog for the filter side. id matches
// ToolsetCatalogEntry.id in toolset-catalog.ts.
const TOOLSET_DEFS = [
  { id: 'browser_automation', prefixes: ['browser_'] },
  { id: 'file_operations', extraTools: ['read_file', 'write_file', 'patch', 'list_directory', 'search_files'] },
  { id: 'terminal', extraTools: ['terminal'] },
  { id: 'code_execution', extraTools: ['execute_code'] },
  { id: 'process_management', extraTools: ['process'] },
  { id: 'skills_system', extraTools: ['skills_list', 'skill_view', 'skill_manage'] },
  { id: 'memory', extraTools: ['memory_retain', 'memory_recall', 'memory_forget'] },
  { id: 'web_tools', extraTools: ['web_search', 'web_extract', 'search_tools'] },
  { id: 'image_generation', extraTools: ['image_generate'] },
  { id: 'text_to_speech', extraTools: ['text_to_speech_tool'] },
  { id: 'messaging', extraTools: ['send_message_tool'] },
  { id: 'scheduled_tasks', extraTools: ['cronjob'] },
  { id: 'agent_delegation', extraTools: ['agent_delegate_tool'] },
  { id: 'computer_use', extraTools: ['computer_use'] },
  { id: 'media_analysis', extraTools: ['vision_analyze'] }
]

const readDisabledToolsets = configPath => readDisabledSet(configPath, 'toolsets')
const invalidateDisabledToolsetsCache = () => invalidateDisabledCache('toolsets')

function toolNamesForToolset(def, availableNames) {
  const names = []
  const seen = new Set()

  if (def.prefixes) {
    for (const prefix of def.prefixes) {
      for (const name of availableNames) {
        if (EXCLUDED_PREFIX_RE.test(name)) continue
        if (name.startsWith(prefix) && !seen.has(name)) {
          names.push(name)
          seen.add(name)
        }
      }
    }
  }

  if (def.extraTools) {
    for (const name of def.extraTools) {
      if (EXCLUDED_PREFIX_RE.test(name)) continue
      if (seen.has(name)) continue
      if (availableNames.has(name)) {
        names.push(name)
        seen.add(name)
      }
    }
  }

  return names
}

// Build the renderer-facing toolset roster. Used by `zast:toolsets:list`.
// `schemas` is the cached Runner tool schemas (`runnerBridge.getTools()`
// already returns `[]` when the Runner isn't ready, so no null check needed).
function buildToolsetRoster(schemas, disabledToolsetIds) {
  const availableNames = new Set(schemas.map(s => s?.name).filter(Boolean))

  return TOOLSET_DEFS.map(def => ({
    id: def.id,
    toolNames: toolNamesForToolset(def, availableNames),
    enabled: !disabledToolsetIds.has(def.id)
  }))
}

module.exports = {
  TOOLSET_DEFS,
  readDisabledToolsets,
  invalidateDisabledToolsetsCache,
  buildToolsetRoster,
  toolNamesForToolset
}
