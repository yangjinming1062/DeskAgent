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
