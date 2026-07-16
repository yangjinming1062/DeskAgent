import type { ZastConfigPutRequest, ZastConfigRecord, ZastConfigResponse } from '@/types/zast'

export function getZastConfig(): Promise<ZastConfigResponse> {
  return window.zastDesktop.api<ZastConfigResponse>({
    path: '/api/config'
  })
}

export function getZastConfigRecord(): Promise<ZastConfigRecord> {
  return window.zastDesktop.api<ZastConfigRecord>({
    path: '/api/config'
  })
}

export function getZastConfigDefaults(): Promise<ZastConfigRecord> {
  return window.zastDesktop.api<ZastConfigRecord>({
    path: '/api/config/defaults'
  })
}

export async function saveZastConfig(config: ZastConfigPutRequest): Promise<{ config: ZastConfigResponse }> {
  const response = await window.zastDesktop.api<{ config: ZastConfigResponse }>({
    body: { config },
    method: 'PUT',
    path: '/api/config'
  })

  return { config: response.config }
}

/** Read a nested section (e.g. `web`, `agent`) as a fresh object copy. Returns `null` if absent
 * or not an object. Use this when you need to spread a sub-section without mutating the source. */
export function pickSection(record: ZastConfigRecord, key: string): Record<string, unknown> | null {
  const value = record[key]

  return value && typeof value === 'object' && !Array.isArray(value) ? { ...(value as object) } : null
}
