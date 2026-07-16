import type { StatusResponse } from '@/types/zast'

export function getStatus(): Promise<StatusResponse> {
  return window.zastDesktop.api<StatusResponse>({
    path: '/api/status'
  })
}
