import type { DeskAgentConfigPutRequest, DeskAgentConfigRecord, DeskAgentConfigResponse } from '@/types/deskagent'

export function getDeskAgentConfig(): Promise<DeskAgentConfigResponse> {
  return window.deskagent.api<DeskAgentConfigResponse>({
    path: '/api/config'
  })
}

export function getDeskAgentConfigRecord(): Promise<DeskAgentConfigRecord> {
  return window.deskagent.api<DeskAgentConfigRecord>({
    path: '/api/config'
  })
}

export function getDeskAgentConfigDefaults(): Promise<DeskAgentConfigRecord> {
  return window.deskagent.api<DeskAgentConfigRecord>({
    path: '/api/config/defaults'
  })
}

export async function saveDeskAgentConfig(config: DeskAgentConfigPutRequest): Promise<{ config: DeskAgentConfigResponse }> {
  const response = await window.deskagent.api<{ config: DeskAgentConfigResponse }>({
    body: { config },
    method: 'PUT',
    path: '/api/config'
  })

  return { config: response.config }
}

/** Read a nested section (e.g. `web`, `agent`) as a fresh object copy. Returns `null` if absent
 * or not an object. Use this when you need to spread a sub-section without mutating the source. */
export function pickSection(record: DeskAgentConfigRecord, key: string): Record<string, unknown> | null {
  const value = record[key]

  return value && typeof value === 'object' && !Array.isArray(value) ? { ...(value as object) } : null
}
