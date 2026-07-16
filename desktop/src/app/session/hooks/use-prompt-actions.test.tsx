import { cleanup, render } from '@testing-library/react'
import type { MutableRefObject } from 'react'
import { useEffect } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ComposerAttachment } from '@/store/composer'
import { $sessions, setSessions } from '@/store/session'
import { $connection } from '@/store/session'
import type { SessionInfo } from '@/types/zast'

import { usePromptActions } from './use-prompt-actions'

vi.mock('@/zast', () => ({
  transcribeAudio: vi.fn()
}))

// The session id the desktop holds is the conversation id returned by
// session.create — the same id used in the URL, the sidebar store, and
// every JSON-RPC payload.
const SESSION_ID = '5'

function sessionInfo(overrides: Partial<SessionInfo> = {}): SessionInfo {
  return {
    ended_at: null,
    id: SESSION_ID,
    input_tokens: 0,
    is_active: true,
    last_active: 0,
    message_count: 3,
    model: null,
    output_tokens: 0,
    preview: null,
    source: null,
    started_at: 0,
    title: 'Old title',
    tool_call_count: 0,
    ...overrides
  }
}

interface HarnessHandle {
  steerPrompt: (text: string) => Promise<boolean>
  submitText: (text: string, options?: { attachments?: ComposerAttachment[]; fromQueue?: boolean }) => Promise<boolean>
}

function Harness({
  busyRef,
  onReady,
  onSeedState,
  refreshSessions,
  requestGateway,
  storedSessionId
}: {
  busyRef?: MutableRefObject<boolean>
  onReady: (handle: HarnessHandle) => void
  onSeedState?: (state: Record<string, unknown>) => void
  refreshSessions: () => Promise<void>
  requestGateway: <T>(method: string, params?: Record<string, unknown>) => Promise<T>
  storedSessionId?: null | string
}) {
  const activeSessionIdRef: MutableRefObject<string | null> = { current: SESSION_ID }

  const selectedStoredSessionIdRef: MutableRefObject<string | null> = {
    current: storedSessionId === undefined ? SESSION_ID : storedSessionId
  }

  const localBusyRef = busyRef ?? { current: false }

  const actions = usePromptActions({
    activeSessionId: SESSION_ID,
    activeSessionIdRef,
    branchCurrentSession: async () => true,
    busyRef: localBusyRef,
    createBackendSessionForSend: async () => SESSION_ID,
    handleSkinCommand: () => '',
    refreshSessions,
    requestGateway,
    selectedStoredSessionIdRef,
    startFreshSessionDraft: () => undefined,
    sttEnabled: false,
    updateSessionState: (_sessionId, updater) => {
      // Seed with interrupted:true so we can prove a fresh submit clears it.
      const next = updater({
        messages: [],
        busy: false,
        awaitingResponse: false,
        interrupted: true
      } as never) as unknown as Record<string, unknown>

      onSeedState?.(next)

      return next as never
    }
  })

  useEffect(() => {
    onReady({ steerPrompt: actions.steerPrompt, submitText: actions.submitText })
  }, [actions.steerPrompt, actions.submitText, onReady])

  return null
}

describe('usePromptActions /title', () => {
  beforeEach(() => {
    setSessions(() => [sessionInfo()])
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('renames via the session.title RPC, updates the sidebar store, and refreshes', async () => {
    const refreshSessions = vi.fn(async () => undefined)

    const requestGateway = vi.fn(
      async (method: string) => (method === 'session.title' ? { pending: false, title: 'New title' } : {}) as never
    )

    let handle: HarnessHandle | null = null
    render(<Harness onReady={h => (handle = h)} refreshSessions={refreshSessions} requestGateway={requestGateway} />)

    await handle!.submitText('/title New title')

    // Routes through session.title with the conversation id — NOT the slash
    // worker (slash.exec) and NOT the REST endpoint.
    expect(requestGateway).toHaveBeenCalledWith('session.title', {
      session_id: SESSION_ID,
      title: 'New title'
    })
    expect(requestGateway).not.toHaveBeenCalledWith('slash.exec', expect.anything())
    expect(refreshSessions).toHaveBeenCalledTimes(1)
    expect($sessions.get()[0]?.title).toBe('New title')
  })

  it('falls through to the slash worker for a bare /title (show current title)', async () => {
    const refreshSessions = vi.fn(async () => undefined)
    const requestGateway = vi.fn(async () => ({ output: 'Title: Old title' }) as never)

    let handle: HarnessHandle | null = null
    render(<Harness onReady={h => (handle = h)} refreshSessions={refreshSessions} requestGateway={requestGateway} />)

    await handle!.submitText('/title')

    expect(requestGateway).not.toHaveBeenCalledWith('session.title', expect.anything())
    expect(requestGateway).toHaveBeenCalledWith('slash.exec', expect.objectContaining({ command: 'title' }))
  })

  it('surfaces a rename error without touching the sidebar store', async () => {
    const refreshSessions = vi.fn(async () => undefined)

    const requestGateway = vi.fn(async (method: string) => {
      if (method === 'session.title') {
        throw new Error('Title too long')
      }

      return {} as never
    })

    let handle: HarnessHandle | null = null
    render(<Harness onReady={h => (handle = h)} refreshSessions={refreshSessions} requestGateway={requestGateway} />)

    await handle!.submitText('/title way too long title')

    expect(requestGateway).toHaveBeenCalledWith(
      'session.title',
      expect.objectContaining({ title: 'way too long title' })
    )
    expect(refreshSessions).not.toHaveBeenCalled()
    expect($sessions.get()[0]?.title).toBe('Old title')
  })
})

describe('usePromptActions submit / queue drain semantics', () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('clears a leftover interrupted flag on a fresh submit (so the new turn streams)', async () => {
    // A leftover ``interrupted: true`` makes ``message.complete`` early-return
    // (``use-message-stream.ts:798``); fresh-submit must reset it BEFORE the
    // request fires.
    const seedStates: Array<Record<string, unknown>> = []
    const requestGateway = vi.fn(async () => ({}) as never)

    let handle: HarnessHandle | null = null
    render(
      <Harness
        onReady={h => (handle = h)}
        onSeedState={state => {
          seedStates.push(state)
        }}
        refreshSessions={async () => undefined}
        requestGateway={requestGateway}
      />
    )

    await handle!.submitText('follow-up after interrupt')

    expect(seedStates.some(s => s.interrupted === false)).toBe(true)
    expect(requestGateway).toHaveBeenCalledWith('prompt.submit', {
      session_id: SESSION_ID,
      text: 'follow-up after interrupt'
    })
  })

  it('a fromQueue drain sends even when busyRef is still true on the settle edge', async () => {
    // busyRef lags $busy by one effect tick on the busy→false settle edge, so a
    // drained queue send would otherwise hit the busy guard and silently no-op.
    const busyRef = { current: true }
    const requestGateway = vi.fn(async () => ({}) as never)

    let handle: HarnessHandle | null = null
    render(
      <Harness
        busyRef={busyRef}
        onReady={h => (handle = h)}
        refreshSessions={async () => undefined}
        requestGateway={requestGateway}
      />
    )

    const accepted = await handle!.submitText('queued message', { fromQueue: true })

    expect(accepted).toBe(true)
    expect(requestGateway).toHaveBeenCalledWith('prompt.submit', {
      session_id: SESSION_ID,
      text: 'queued message'
    })
  })

  it('a normal (non-queue) submit still respects the busyRef guard', async () => {
    const busyRef = { current: true }
    const requestGateway = vi.fn(async () => ({}) as never)

    let handle: HarnessHandle | null = null
    render(
      <Harness
        busyRef={busyRef}
        onReady={h => (handle = h)}
        refreshSessions={async () => undefined}
        requestGateway={requestGateway}
      />
    )

    const accepted = await handle!.submitText('should be blocked')

    expect(accepted).toBe(false)
    expect(requestGateway).not.toHaveBeenCalledWith('prompt.submit', expect.anything())
  })
})

describe('usePromptActions steerPrompt', () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('injects the trimmed text via session.steer and reports acceptance on a queued status', async () => {
    const requestGateway = vi.fn(async () => ({ status: 'queued' }) as never)

    let handle: HarnessHandle | null = null
    render(
      <Harness onReady={h => (handle = h)} refreshSessions={async () => undefined} requestGateway={requestGateway} />
    )

    const accepted = await handle!.steerPrompt('  nudge the run  ')

    expect(accepted).toBe(true)
    // Steer never starts a turn — it rides the live run via session.steer only.
    expect(requestGateway).toHaveBeenCalledWith('session.steer', {
      session_id: SESSION_ID,
      text: 'nudge the run'
    })
    expect(requestGateway).not.toHaveBeenCalledWith('prompt.submit', expect.anything())
  })

  it('reports rejection (so the caller queues) when the gateway has no live tool window', async () => {
    const requestGateway = vi.fn(async () => ({ status: 'rejected' }) as never)

    let handle: HarnessHandle | null = null
    render(
      <Harness onReady={h => (handle = h)} refreshSessions={async () => undefined} requestGateway={requestGateway} />
    )

    expect(await handle!.steerPrompt('too late')).toBe(false)
  })

  it('reports rejection (never throws) when the steer RPC errors', async () => {
    const requestGateway = vi.fn(async () => {
      throw new Error('agent does not support steer')
    })

    let handle: HarnessHandle | null = null
    render(
      <Harness onReady={h => (handle = h)} refreshSessions={async () => undefined} requestGateway={requestGateway} />
    )

    expect(await handle!.steerPrompt('boom')).toBe(false)
  })

  it('skips the RPC entirely for empty text', async () => {
    const requestGateway = vi.fn(async () => ({ status: 'queued' }) as never)

    let handle: HarnessHandle | null = null
    render(
      <Harness onReady={h => (handle = h)} refreshSessions={async () => undefined} requestGateway={requestGateway} />
    )

    expect(await handle!.steerPrompt('   ')).toBe(false)
    expect(requestGateway).not.toHaveBeenCalled()
  })
})

describe('usePromptActions file attachment sync', () => {
  afterEach(() => {
    cleanup()
    $connection.set(null)
    vi.restoreAllMocks()
  })

  function fileAttachment(): ComposerAttachment {
    return {
      id: 'file:report.txt',
      kind: 'file',
      label: 'report.txt',
      path: '/Users/alice/Downloads/report.txt',
      refText: '@file:`/Users/alice/Downloads/report.txt`'
    }
  }

  it('passes the file path reference directly to prompt.submit without file.attach', async () => {
    const calls: { method: string; params?: Record<string, unknown> }[] = []

    const requestGateway = vi.fn(async (method: string, params?: Record<string, unknown>) => {
      calls.push({ method, params })

      return {} as never
    })

    let handle: HarnessHandle | null = null
    render(
      <Harness onReady={h => (handle = h)} refreshSessions={async () => undefined} requestGateway={requestGateway} />
    )

    const ok = await handle!.submitText('summarize', { attachments: [fileAttachment()] })

    expect(ok).toBe(true)
    expect(calls.length).toBe(1)
    expect(calls[0]).toEqual({
      method: 'prompt.submit',
      params: { session_id: SESSION_ID, text: '@file:/Users/alice/Downloads/report.txt\n\nsummarize' }
    })
  })
})

describe('usePromptActions sleep/wake session recovery', () => {
  const SESSION_ID_A = '5'

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('resumes the stored session and retries once when prompt.submit reports "session not found"', async () => {
    // After sleep/wake the gateway's in-memory session table is cleared, so the
    // first prompt.submit with the stale id fails. The hook re-runs
    // session.resume to remount the in-memory runtime, then retries the send
    // transparently.
    const calls: { method: string; params?: Record<string, unknown> }[] = []
    let submitAttempts = 0

    const requestGateway = vi.fn(async (method: string, params?: Record<string, unknown>) => {
      calls.push({ method, params })

      if (method === 'prompt.submit') {
        submitAttempts += 1

        if (submitAttempts === 1) {
          throw new Error('session not found')
        }

        return {} as never
      }

      if (method === 'session.resume') {
        return { session_id: SESSION_ID_A } as never
      }

      return {} as never
    })

    let handle: HarnessHandle | null = null
    render(
      <Harness
        onReady={h => (handle = h)}
        refreshSessions={async () => undefined}
        requestGateway={requestGateway}
        storedSessionId={SESSION_ID_A}
      />
    )

    const ok = await handle!.submitText('message after wake')

    expect(ok).toBe(true)
    // First submit (stale id) → session.resume (stored id) → retry submit.
    expect(calls.map(c => c.method)).toEqual(['prompt.submit', 'session.resume', 'prompt.submit'])
    expect(calls[1]?.params).toEqual({ session_id: SESSION_ID_A })
    expect(calls[2]?.params).toEqual({ session_id: SESSION_ID_A, text: 'message after wake' })
  })

  it('surfaces the original error (no resume) when the failure is not "session not found"', async () => {
    const calls: string[] = []
    const states: Record<string, unknown>[] = []

    const requestGateway = vi.fn(async (method: string) => {
      calls.push(method)

      if (method === 'prompt.submit') {
        throw new Error('session busy')
      }

      return {} as never
    })

    let handle: HarnessHandle | null = null
    render(
      <Harness
        onReady={h => (handle = h)}
        onSeedState={s => states.push(s)}
        refreshSessions={async () => undefined}
        requestGateway={requestGateway}
        storedSessionId={SESSION_ID_A}
      />
    )

    // submitText swallows the error into an inline bubble and returns false.
    expect(await handle!.submitText('message')).toBe(false)
    // No resume attempt for a non-recoverable error.
    expect(calls).not.toContain('session.resume')
  })

  it('surfaces "session not found" (no resume) when there is no stored session id', async () => {
    const calls: string[] = []

    const requestGateway = vi.fn(async (method: string) => {
      calls.push(method)

      if (method === 'prompt.submit') {
        throw new Error('session not found')
      }

      return {} as never
    })

    let handle: HarnessHandle | null = null
    render(
      <Harness
        onReady={h => (handle = h)}
        refreshSessions={async () => undefined}
        requestGateway={requestGateway}
        storedSessionId={null}
      />
    )

    // With a null stored ref, the `&& selectedStoredSessionIdRef.current` guard
    // short-circuits — no resume is attempted and the error surfaces normally.
    expect(await handle!.submitText('message')).toBe(false)
    expect(calls).not.toContain('session.resume')
  })
})
