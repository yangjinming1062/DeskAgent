import type {
  SpiritAgentConfigPutRequest,
  SpiritAgentConfigRecord,
  SpiritAgentConfigResponse
} from '@/shared/types/spiritagent'

export function getSpiritAgentConfig(): Promise<SpiritAgentConfigResponse> {
  return window.spiritagent.api<SpiritAgentConfigResponse>({
    path: '/api/config'
  })
}

export function getSpiritAgentConfigDefaults(): Promise<SpiritAgentConfigRecord> {
  return window.spiritagent.api<SpiritAgentConfigRecord>({
    path: '/api/config/defaults'
  })
}

export async function saveSpiritAgentConfig(
  config: SpiritAgentConfigPutRequest
): Promise<{ config: SpiritAgentConfigResponse }> {
  const response = await window.spiritagent.api<{ config: SpiritAgentConfigResponse }>({
    body: { config },
    method: 'PUT',
    path: '/api/config'
  })

  return { config: response.config }
}
