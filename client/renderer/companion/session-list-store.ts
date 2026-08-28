import { atom } from 'nanostores'

import {
  $chatSessionId,
  $sessionSettings,
  hydrateChatMessages,
  resetChatMessages,
  resetSessionContextUsage,
  setChatSession
} from '@/companion/chat-store'
import { log } from '@/shared/lib/log'
import { persistString, storedString } from '@/shared/lib/storage'
import { $gateway } from '@/shared/store/gateway'
import type { SessionInfo, SessionResumeResponse } from '@/shared/types/spiritagent'

export type SessionSort = 'created' | 'messages' | 'recent'

const SESSION_SORTS: readonly SessionSort[] = ['recent', 'created', 'messages']
const SESSION_SORT_KEY = 'da.companion.sessionSort'

export const $sessions = atom<SessionInfo[]>([])
export const $sessionsLoading = atom(false)
export const $sessionListOpen = atom(false)
export const $sessionSort = atom<SessionSort>(parseSessionSort(storedString(SESSION_SORT_KEY)))
export const $sessionSearch = atom('')
export const $searchResults = atom<SessionInfo[]>([])
export const $searchLoading = atom(false)
export const $archivedSessions = atom<SessionInfo[]>([])
export const $archivedLoading = atom(false)
export const $archiveOpen = atom(false)

// 每个 fetch 系列各自自增，避免慢响应覆盖更新的结果。
let sessionsToken = 0
let archivedToken = 0
let searchToken = 0

function parseSessionSort(raw: null | string): SessionSort {
  return SESSION_SORTS.includes(raw as SessionSort) ? (raw as SessionSort) : 'recent'
}

// 聊天窗头部标题兜底链：主列表 → 归档列表 → 搜索结果（归档/搜索命中的会话不在主列表里）。
export function findSessionInfo(sessionId: string): SessionInfo | undefined {
  return (
    $sessions.get().find(s => s.id === sessionId) ??
    $archivedSessions.get().find(s => s.id === sessionId) ??
    $searchResults.get().find(s => s.id === sessionId)
  )
}

export function setSessionListOpen(open: boolean): void {
  $sessionListOpen.set(open)

  if (open) {
    void fetchSessions()
    void fetchArchived()
  }
}

export function setSessionSort(sort: SessionSort): void {
  if (sort === $sessionSort.get()) {
    return
  }

  $sessionSort.set(sort)
  persistString(SESSION_SORT_KEY, sort)

  if ($sessionListOpen.get()) {
    void fetchSessions()
  }
}

async function fetchSessions(): Promise<void> {
  const token = ++sessionsToken
  $sessionsLoading.set(true)

  try {
    const res = await window.spiritagent.api<{ sessions: SessionInfo[] }>({
      path: `/api/sessions?order=${$sessionSort.get()}`
    })

    if (token === sessionsToken) {
      $sessions.set(res.sessions || [])
    }
  } catch (err) {
    log.error('session-list', 'Failed to fetch sessions:', err)
  } finally {
    if (token === sessionsToken) {
      $sessionsLoading.set(false)
    }
  }
}

async function fetchArchived(): Promise<void> {
  const token = ++archivedToken
  $archivedLoading.set(true)

  try {
    const res = await window.spiritagent.api<{ sessions: SessionInfo[] }>({
      path: '/api/sessions?archived=only&limit=100'
    })

    if (token === archivedToken) {
      $archivedSessions.set(res.sessions || [])
    }
  } catch (err) {
    log.error('session-list', 'Failed to fetch archived sessions:', err)
  } finally {
    if (token === archivedToken) {
      $archivedLoading.set(false)
    }
  }
}

export async function runSessionSearch(query: string): Promise<void> {
  const q = query.trim()

  if (!q) {
    searchToken++
    $searchResults.set([])
    $searchLoading.set(false)

    return
  }

  const token = ++searchToken
  $searchLoading.set(true)

  try {
    const res = await window.spiritagent.api<{ sessions: SessionInfo[] }>({
      path: `/api/sessions/search?q=${encodeURIComponent(q)}&archived=include`
    })

    if (token === searchToken) {
      $searchResults.set(res.sessions || [])
    }
  } catch (err) {
    log.error('session-list', 'Failed to search sessions:', err)
  } finally {
    if (token === searchToken) {
      $searchLoading.set(false)
    }
  }
}

async function patchSession(sessionId: string, body: { archived?: boolean; pinned?: boolean }): Promise<boolean> {
  try {
    await window.spiritagent.api({ body, method: 'PATCH', path: `/api/sessions/${sessionId}` })

    return true
  } catch (err) {
    log.error('session-list', 'Failed to patch session:', err)

    return false
  }
}

export async function pinSession(sessionId: string, pinned: boolean): Promise<void> {
  if (!(await patchSession(sessionId, { pinned }))) {
    return
  }

  void fetchSessions()
}

export async function archiveSession(sessionId: string, archived: boolean): Promise<void> {
  if (!(await patchSession(sessionId, { archived }))) {
    return
  }

  // 归档的若是当前会话，切回主对话，避免聊天窗停在一个已收起的对话上。
  if (archived && $chatSessionId.get() === sessionId) {
    await openMainSession()
  }

  void fetchSessions()
  void fetchArchived()
}

export async function createNewSession(): Promise<string | null> {
  const gw = $gateway.get()

  if (!gw) {
    return null
  }

  try {
    const res = await gw.request<{ session_id: string; info?: SessionResumeResponse['info'] }>('session.create', {})
    setChatSession(res.session_id)
    resetChatMessages()

    if (res.info?.settings) {
      $sessionSettings.set(res.info.settings)
    }

    resetSessionContextUsage(res.info?.context_window)
    void fetchSessions()

    return res.session_id
  } catch (err) {
    log.error('session-list', 'Failed to create session:', err)

    return null
  }
}

/** 从源会话的某条消息派生新会话：调用 session.fork RPC，命中后立即自动挂载新会话并 hydrate 历史（末条带 draft 锚点 → 显示「未发送」徽标）。
 * 失败返回 null，错误已记录日志。 */
export async function forkConversation(sourceSessionId: string, sourceMessageId: number): Promise<string | null> {
  const gw = $gateway.get()

  if (!gw) {
    return null
  }

  try {
    const res = await gw.request<SessionResumeResponse>('session.fork', {
      source_session_id: sourceSessionId,
      source_message_id: sourceMessageId
    })

    // 与 switchSession 同一形态：先 setChatSession 清残留状态 + 持久化新 id，再 hydrate 灌消息流
    setChatSession(res.session_id)
    hydrateChatMessages(res.messages || [], res.info)

    if (res.info?.settings) {
      $sessionSettings.set(res.info.settings as Parameters<typeof $sessionSettings.set>[0])
    }

    resetSessionContextUsage(res.info?.context_window)
    // 刷新抽屉让新会话出现在列表（默认按 parent_id 隐藏，开 include_subagents 才能看到）
    void fetchSessions()

    return res.session_id
  } catch (err) {
    log.error('session-list', 'Failed to fork session:', err)

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
    hydrateChatMessages(res.messages || [], res.info)
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
    hydrateChatMessages(res.messages || [], res.info)
    onMounted?.(res)

    return res.session_id
  } catch (err) {
    log.error('session-list', 'Failed to open main session:', err)

    return null
  }
}

export async function deleteSession(sessionId: string): Promise<void> {
  try {
    await window.spiritagent.api({ method: 'DELETE', path: `/api/sessions/${sessionId}` })
  } catch (err) {
    log.error('session-list', 'Failed to delete session:', err)

    return
  }

  if ($chatSessionId.get() === sessionId) {
    await openMainSession()
  }

  void fetchSessions()
  void fetchArchived()
}
