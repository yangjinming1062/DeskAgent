import type * as React from 'react'

import type { ChatMessage } from '@/lib/chat-messages'

export interface ContextSuggestion {
  text: string
  display: string
  meta?: string
}

export interface ImageAttachResponse {
  attached?: boolean
  path?: string
  text?: string
  message?: string
  count?: number
  bytes?: number
  name?: string
  width?: number
  height?: number
  token_estimate?: number
}

export interface ImageDetachResponse {
  detached?: boolean
  count?: number
}

export interface SlashExecResponse {
  output?: string
  warning?: string
}

export interface SessionSteerResponse {
  // 'queued' == accepted into the live turn's steer slot (injected at the next
  // tool-result boundary); 'rejected' == no live tool window, caller queues.
  status?: 'queued' | 'rejected'
  text?: string
}

export interface SessionTitleResponse {
  title?: string
}

export interface ExecCommandDispatchResponse {
  type: 'exec' | 'plugin'
  output?: string
}

export interface AliasCommandDispatchResponse {
  type: 'alias'
  target: string
}

export interface SkillCommandDispatchResponse {
  type: 'skill'
  name: string
  message?: string
}

export interface SendCommandDispatchResponse {
  type: 'send'
  message: string
}

export type CommandDispatchResponse =
  | ExecCommandDispatchResponse
  | AliasCommandDispatchResponse
  | SkillCommandDispatchResponse
  | SendCommandDispatchResponse

export type SidebarNavId = 'artifacts' | 'insights' | 'new-session'

export interface SidebarNavItem {
  id: SidebarNavId
  label: string
  icon: React.ComponentType<{ className?: string }>
  route?: string
  action?: 'new-session'
}

export interface ClientSessionState {
  storedSessionId: string | null
  messages: ChatMessage[]
  branch: string
  cwd: string
  busy: boolean
  awaitingResponse: boolean
  streamId: string | null
  sawAssistantPayload: boolean
  pendingBranchGroup: string | null
  interrupted: boolean
  /** A blocking clarify prompt is waiting on the user for this session. Drives
   *  the sidebar "needs input" indicator; cleared when the turn resumes/ends. */
  needsInput: boolean
  /** Epoch ms the current turn started, or null when idle. Per-session so a
   *  background turn's elapsed timer keeps counting while another session is
   *  focused, and switching sessions doesn't zero a still-running turn's clock.
   *  The global $turnStartedAt mirrors whichever session is currently viewed. */
  turnStartedAt: number | null
}
