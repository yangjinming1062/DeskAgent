import type { StatusResponse } from '@/types/deskagent'

export function getStatus(): Promise<StatusResponse> {
  return window.deskagent.api<StatusResponse>({
    path: '/api/status'
  })
}
