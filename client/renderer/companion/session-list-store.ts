import { atom } from 'nanostores'

import { $chatSessionId, hydrateChatMessages, resetChatMessages, setChatSession } from '@/companion/chat-store'
import { log } from '@/shared/lib/log'
import { $gateway } from '@/shared/store/gateway'
import type { SessionInfo, SessionResumeResponse } from '@/shared/types/spiritagent'

export const $sessions = atom<SessionInfo[]>([])
export const $sessionsLoading = atom(false)
export const $sessionListOpen = atom(false)

// 每次 fetch 自增，避免慢响应覆盖更新的结果。
let fetchToken = 0

export function setSessionListOpen(open: boolean): void {
  $sessionListOpen.set(open)

  if (open) {
    void fetchSessions()
  }
}

export async function fetchSessions(): Promise<void> {
  const token = ++fetchToken
  $sessionsLoading.set(true)

  try {
    const res = await window.spiritagent.api<{ sessions: SessionInfo[] }>({ path: '/api/v1/sessions' })

    if (token === fetchToken) {
      $sessions.set(res.sessions || [])
    }
  } catch (err) {
    log.error('session-list', 'Failed to fetch sessions:', err)
  } finally {
    if (token === fetchToken) {
      $sessionsLoading.set(false)
    }
  }
}

export async function createNewSession(): Promise<string | null> {
  const gw = $gateway.get()

  if (!gw) {
    return null
  }

  try {
    const res = await gw.request<{ session_id: string }>('session.create', {})
    setChatSession(res.session_id)
    resetChatMessages()
    void fetchSessions()

    return res.session_id
  } catch (err) {
    log.error('session-list', 'Failed to create session:', err)

    return null
  }
}

export async function switchSession(sessionId: string): Promise<void> {
  const gw = $gateway.get()

  if (!gw) {
    return
  }

  try {
    const res = await gw.request<SessionResumeResponse>('session.resume', { session_id: sessionId })

    setChatSession(sessionId)
    hydrateChatMessages(res.messages || [])
  } catch (err) {
    log.error('session-list', 'Failed to switch session:', err)
  }
}

// 挂载主会话并加载其对话流。
export async function openMainSession(onMounted?: (res: SessionResumeResponse) => void): Promise<string | null> {
  const gw = $gateway.get()

  if (!gw) {
    return null
  }

  try {
    const res = await gw.request<SessionResumeResponse>('session.get_main')
    setChatSession(res.session_id)
    hydrateChatMessages(res.messages || [])
    onMounted?.(res)

    return res.session_id
  } catch (err) {
    log.error('session-list', 'Failed to open main session:', err)

    return null
  }
}

export async function deleteSession(sessionId: string): Promise<void> {
  try {
    await window.spiritagent.api({ method: 'DELETE', path: `/api/v1/sessions/${sessionId}` })
  } catch (err) {
    log.error('session-list', 'Failed to delete session:', err)

    return
  }

  if ($chatSessionId.get() === sessionId) {
    await openMainSession()
  }

  void fetchSessions()
}
