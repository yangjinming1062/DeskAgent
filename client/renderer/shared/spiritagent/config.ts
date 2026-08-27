import type { SpiritAgentConfigPutRequest, SpiritAgentConfigResponse } from '@/shared/types/spiritagent'

export function getSpiritAgentConfig(): Promise<SpiritAgentConfigResponse> {
  return window.spiritagent.api<SpiritAgentConfigResponse>({
    path: '/api/config'
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
