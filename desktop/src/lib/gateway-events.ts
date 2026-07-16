interface RpcEventLike {
  payload?: unknown
  type?: string
}

function asRecord(payload: unknown): Record<string, unknown> {
  return payload && typeof payload === 'object' ? (payload as Record<string, unknown>) : {}
}

/**
 * Whether an unscoped event (no `session_id`) must be dropped rather than
 * attributed to the focused chat.
 *
 * Only `subagent.*` qualifies: it describes background/async work that must
 * never attach to whichever chat happens to be focused. Every other scoped
 * event — message/reasoning/thinking/tool/status/prompt — is, when unscoped,
 * the active turn's own output. The gateway always stamps a *background*
 * session's events with that session's id, so a missing id can only mean "the
 * focused turn". #42178 dropped those too, which silently swallowed the live
 * answer; it then reappeared only after a transcript refetch (manual refresh).
 */
export function gatewayEventRequiresSessionId(eventType: string | undefined): boolean {
  return eventType?.startsWith('subagent.') ?? false
}

export function gatewayEventCompletedFileDiff(event: RpcEventLike): boolean {
  if (event.type !== 'tool.complete') {
    return false
  }

  const diff = asRecord(event.payload).inline_diff

  return typeof diff === 'string' && diff.trim().length > 0
}
