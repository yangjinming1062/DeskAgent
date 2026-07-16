import type { PaginatedSessions, SessionMessagesResponse, SessionSearchResponse } from '@/types/zast'

const SESSION_LIST_REQUEST_TIMEOUT_MS = 60_000

export async function listSessions(
  limit = 40,
  minMessages = 0,
  archived: 'exclude' | 'include' | 'only' = 'exclude',
  order: 'created' | 'recent' = 'recent',
  includeSubagents = false
): Promise<PaginatedSessions> {
  const subagents = includeSubagents ? '&include_subagents=true' : ''

  const result = await window.zastDesktop.api<PaginatedSessions>({
    path: `/api/sessions?limit=${limit}&offset=0&min_messages=${Math.max(0, minMessages)}&archived=${archived}&order=${order}${subagents}`,
    timeoutMs: SESSION_LIST_REQUEST_TIMEOUT_MS
  })

  return {
    ...result,
    sessions: result.sessions.slice(0, limit),
    offset: 0
  }
}

export function setSessionArchived(id: string, archived: boolean): Promise<{ ok: boolean }> {
  return window.zastDesktop.api<{ ok: boolean }>({
    path: `/api/sessions/${encodeURIComponent(id)}`,
    method: 'PATCH',
    body: { archived }
  })
}

export function searchSessions(query: string): Promise<SessionSearchResponse> {
  return window.zastDesktop.api<SessionSearchResponse>({
    path: `/api/sessions/search?q=${encodeURIComponent(query)}`
  })
}

export function getSessionMessages(id: string): Promise<SessionMessagesResponse> {
  return window.zastDesktop.api<SessionMessagesResponse>({
    path: `/api/sessions/${encodeURIComponent(id)}/messages`
  })
}

export function deleteSession(id: string): Promise<{ ok: boolean }> {
  return window.zastDesktop.api<{ ok: boolean }>({
    path: `/api/sessions/${encodeURIComponent(id)}`,
    method: 'DELETE'
  })
}

export function renameSession(id: string, title: string): Promise<{ ok: boolean; title: string }> {
  return window.zastDesktop.api<{ ok: boolean; title: string }>({
    path: `/api/sessions/${encodeURIComponent(id)}`,
    method: 'PATCH',
    body: { title }
  })
}
