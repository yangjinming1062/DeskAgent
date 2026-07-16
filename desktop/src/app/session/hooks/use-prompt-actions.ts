import type { AppendMessage, ThreadMessage } from '@assistant-ui/react'
import { type MutableRefObject, useCallback } from 'react'

import { translateNow, type Translations, useI18n } from '@/i18n'
import { branchGroupForUser, type ChatMessage, chatMessageText, textPart } from '@/lib/chat-messages'
import {
  attachmentDisplayText,
  parseCommandDispatch,
  parseSlashCommand,
  pathLabel,
  SLASH_COMMAND_RE
} from '@/lib/chat-runtime'
import {
  type CommandsCatalogLike,
  desktopSlashUnavailableMessage,
  filterDesktopCommandsCatalog,
  isDesktopSlashCommand,
  isModelPickerCommand
} from '@/lib/desktop-slash-commands'
import { ZastRpcError, ZastRpcErrorCode } from '@/lib/gateway-protocol/json-rpc-gateway'
import { triggerHaptic } from '@/lib/haptics'
import { setMutableRef } from '@/lib/mutable-ref'
import { isProviderSetupErrorMessage } from '@/lib/provider-setup-errors'
import { setSessionYolo } from '@/lib/yolo-session'
import {
  $composerAttachments,
  addComposerAttachment,
  clearComposerAttachments,
  type ComposerAttachment,
  terminalContextBlocksFromDraft
} from '@/store/composer'
import { clearNotifications, notify, notifyError } from '@/store/notifications'
import {
  $busy,
  $messages,
  $yoloActive,
  setAwaitingResponse,
  setBusy,
  setMessages,
  setModelPickerOpen,
  setSessions,
  setYoloActive
} from '@/store/session'
import { transcribeAudio } from '@/zast'

import type {
  ClientSessionState,
  ImageAttachResponse,
  SessionSteerResponse,
  SessionTitleResponse,
  SlashExecResponse
} from '../../types'

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()

    reader.addEventListener('load', () => {
      if (typeof reader.result === 'string') {
        resolve(reader.result)
      } else {
        reject(new Error(translateNow('desktop.audioReadFailed')))
      }
    })
    reader.addEventListener('error', () => reject(reader.error || new Error(translateNow('desktop.audioReadFailed'))))
    reader.readAsDataURL(blob)
  })
}

export interface PromptSubmitPayload extends Record<string, unknown> {
  session_id: string
  text: string
  attachments?: Array<{ type: 'video'; file_url: string }>
}

// Map renderer-side composer attachments into the JSON-RPC payload. Single
// source of truth so the first attempt and the session.resume retry path
// don't drift apart when new attachment kinds are added.
export function buildPromptPayload(
  sessionId: string,
  text: string,
  attachments: readonly ComposerAttachment[]
): PromptSubmitPayload {
  const payload: PromptSubmitPayload = { session_id: sessionId, text }

  const videoAttachments = attachments
    .filter((a): a is ComposerAttachment & { fileUrl: string } => a.kind === 'video' && Boolean(a.fileUrl))
    .map(a => ({ type: 'video' as const, file_url: a.fileUrl }))

  if (videoAttachments.length > 0) {
    payload.attachments = videoAttachments
  }

  return payload
}

function isProviderSetupError(error: unknown) {
  const message = error instanceof Error ? error.message : String(error)

  return isProviderSetupErrorMessage(message)
}

function inlineErrorMessage(error: unknown, fallback: string): string {
  // ``ZastRpcError`` already carries the wire-encoded backend payload (no IPC
  // wrapper to strip); the code only steers extra framing copy here — the wire
  // ``message`` is preserved verbatim so operators can see *which* method or
  // parameter the backend rejected, instead of every failure collapsing onto
  // a fixed English string.
  if (error instanceof ZastRpcError) {
    const wire = (error.message ?? '').trim()

    if (error.code === ZastRpcErrorCode.MethodNotFound) {
      return wire
        ? `Method not implemented — please update the desktop client. (${wire})`
        : 'Method not implemented — please update the desktop client.'
    }

    return wire || fallback
  }

  const raw = error instanceof Error ? error.message : typeof error === 'string' ? error : fallback

  return (raw.match(/Error invoking remote method '[^']+': Error: (.+)$/)?.[1] ?? raw).replace(/^Error:\s*/, '').trim()
}

function base64FromDataUrl(dataUrl: string): string {
  const comma = dataUrl.indexOf(',')

  return comma >= 0 ? dataUrl.slice(comma + 1) : ''
}

function imageFilenameFromPath(filePath: string): string {
  return filePath.split(/[\\/]/).filter(Boolean).pop() || 'image.png'
}

interface PromptActionsOptions {
  activeSessionId: string | null
  activeSessionIdRef: MutableRefObject<string | null>
  busyRef: MutableRefObject<boolean>
  branchCurrentSession: () => Promise<boolean>
  createBackendSessionForSend: (preview?: string | null) => Promise<string | null>
  handleSkinCommand: (arg: string) => string
  refreshSessions: () => Promise<void>
  requestGateway: <T>(method: string, params?: Record<string, unknown>) => Promise<T>
  selectedStoredSessionIdRef: MutableRefObject<string | null>
  startFreshSessionDraft: () => void
  sttEnabled: boolean
  updateSessionState: (
    sessionId: string,
    updater: (state: ClientSessionState) => ClientSessionState,
    storedSessionId?: string | null
  ) => ClientSessionState
}

interface SubmitTextOptions {
  attachments?: ComposerAttachment[]
  fromQueue?: boolean
}

function renderCommandsCatalog(catalog: CommandsCatalogLike, copy: Translations['desktop']): string {
  const desktopCatalog = filterDesktopCommandsCatalog(catalog)

  const sections = desktopCatalog.categories?.length
    ? desktopCatalog.categories
    : [{ name: copy.desktopCommands, pairs: desktopCatalog.pairs ?? [] }]

  const body = sections
    .filter(section => section.pairs.length > 0)
    .map(section => {
      const rows = section.pairs.map(([cmd, desc]) => `${cmd.padEnd(18)} ${desc}`)

      return [`${section.name}:`, ...rows].join('\n')
    })
    .join('\n\n')

  const tail = [
    desktopCatalog.skill_count ? copy.skillCommandsAvailable(desktopCatalog.skill_count) : '',
    desktopCatalog.warning ? copy.warningLine(desktopCatalog.warning) : ''
  ]
    .filter(Boolean)
    .join('\n')

  return [body || 'No desktop commands available.', tail].filter(Boolean).join('\n\n')
}

function slashStatusText(command: string, output: string): string {
  return [`slash:${command}`, output.trim()].filter(Boolean).join('\n')
}

function appendText(message: AppendMessage): string {
  return message.content
    .map(part => ('text' in part ? part.text : ''))
    .join('')
    .trim()
}

function visibleUserOrdinal(messages: readonly ChatMessage[], end: number): number {
  return messages.slice(0, end).filter(m => m.role === 'user' && !m.hidden).length
}

export function usePromptActions({
  activeSessionId,
  activeSessionIdRef,
  busyRef,
  branchCurrentSession,
  createBackendSessionForSend,
  handleSkinCommand,
  refreshSessions,
  requestGateway,
  selectedStoredSessionIdRef,
  startFreshSessionDraft,
  sttEnabled,
  updateSessionState
}: PromptActionsOptions) {
  const { t } = useI18n()
  const copy = t.desktop

  const appendSessionTextMessage = useCallback(
    (sessionId: string, role: ChatMessage['role'], text: string) => {
      const body = text.trim()

      if (!body) {
        return
      }

      updateSessionState(
        sessionId,
        state => ({
          ...state,
          messages: [
            ...state.messages,
            {
              id: `${role}-${crypto.randomUUID()}`,
              role,
              parts: [textPart(body)]
            }
          ]
        }),
        selectedStoredSessionIdRef.current
      )
    },
    [selectedStoredSessionIdRef, updateSessionState]
  )

  const syncAttachmentsForSubmit = useCallback(
    async (
      sessionId: string,
      attachments: ComposerAttachment[],
      options: { updateComposerAttachments?: boolean } = {}
    ): Promise<ComposerAttachment[]> => {
      const updateComposerAttachments = options.updateComposerAttachments ?? true
      const synced: ComposerAttachment[] = []

      for (const attachment of attachments) {
        // Already-synced or pathless refs (terminal, url, etc.) pass through.
        if (!attachment.path || attachment.attachedSessionId === sessionId) {
          synced.push(attachment)

          continue
        }

        if (attachment.kind === 'image') {
          const result = await requestGateway<ImageAttachResponse>('image.attach', {
            session_id: sessionId,
            path: attachment.path
          })

          if (!result.attached) {
            const label = attachment.label || pathLabel(attachment.path)
            throw new Error(result.message || `Could not attach ${label}`)
          }

          const attachedPath = result.path || attachment.path

          const nextAttachment: ComposerAttachment = {
            ...attachment,
            id: attachment.id,
            label: attachedPath ? pathLabel(attachedPath) : attachment.label,
            path: attachedPath,
            attachedSessionId: sessionId
          }

          if (updateComposerAttachments) {
            addComposerAttachment(nextAttachment)
          }

          synced.push(nextAttachment)

          continue
        }

        if (attachment.kind === 'file') {
          // Non-image file refs are @file: paths the gateway reads with its file
          // tools. Since file tools always run on the local runner, we never
          // need to upload text files to the backend, even in remote mode.
          // The LLM will use the runner's read_file tool to read the original path.
          const refText = `@file:${attachment.path}`

          const nextAttachment: ComposerAttachment = {
            ...attachment,
            refText,
            attachedSessionId: sessionId
          }

          if (updateComposerAttachments) {
            addComposerAttachment(nextAttachment)
          }

          synced.push(nextAttachment)

          continue
        }

        synced.push(attachment)
      }

      return synced
    },
    [requestGateway]
  )

  const submitPromptText = useCallback(
    async (rawText: string, options?: SubmitTextOptions) => {
      const visibleText = rawText.trim()
      const usingComposerAttachments = !options?.attachments
      const attachments = options?.attachments ?? $composerAttachments.get()

      const terminalContextBlocks = terminalContextBlocksFromDraft(rawText).join('\n\n')
      const hasImage = attachments.some(a => a.kind === 'image')

      // Refs are recomputed after sync so any path-based replacements
      // take effect before we stringify the context block.
      // optimistic message with the pre-sync refs, then rewrite once synced.
      let attachmentRefs = attachments.map(attachmentDisplayText).filter((r): r is string => Boolean(r))

      const buildContextText = (atts: ComposerAttachment[]): string => {
        const contextRefs = atts
          .map(a => a.refText)
          .filter(Boolean)
          .join('\n')

        return (
          [contextRefs, terminalContextBlocks, visibleText].filter(Boolean).join('\n\n') ||
          (atts.some(a => a.kind === 'image') ? 'What do you see in this image?' : '')
        )
      }

      // Queue drains fire on the busy→false settle edge, where busyRef (synced
      // from $busy by a separate effect) may still read true — honoring it would
      // bounce the drained send. The drain lock serializes them; the user path
      // keeps the guard so a stray Enter mid-turn can't double-submit.
      const hasSendable = Boolean(visibleText || terminalContextBlocks || attachments.length || hasImage)

      if (!hasSendable || (!options?.fromQueue && busyRef.current)) {
        return false
      }

      const optimisticId = `user-${crypto.randomUUID()}`

      const buildUserMessage = (): ChatMessage => ({
        id: optimisticId,
        role: 'user',
        parts: [textPart(visibleText || (attachmentRefs.length ? '' : attachments.map(a => a.label).join(', ')))],
        attachmentRefs
      })

      const releaseBusy = () => {
        setMutableRef(busyRef, false)
        setBusy(false)
        setAwaitingResponse(false)
      }

      // Idempotent optimistic insert — re-running with the resolved sessionId
      // after createBackendSessionForSend just overwrites with the same id.
      const seedOptimistic = (sid: string) =>
        updateSessionState(
          sid,
          state => ({
            ...state,
            messages: state.messages.some(m => m.id === optimisticId)
              ? state.messages
              : [...state.messages, buildUserMessage()],
            busy: true,
            awaitingResponse: true,
            pendingBranchGroup: null,
            sawAssistantPayload: false,
            // Fresh submit = new turn — clear any leftover interrupt flag, else
            // mutateStream/completeAssistantMessage drop every delta of this turn
            // (what made drained-after-interrupt sends go silent).
            interrupted: false
          }),
          selectedStoredSessionIdRef.current
        )

      // After sync rewrites refs, refresh the optimistic message in place so the
      // transcript shows the resolved @file: ref rather than the local path.
      const rewriteOptimistic = (sid: string) =>
        updateSessionState(
          sid,
          state => ({
            ...state,
            messages: state.messages.map(message => (message.id === optimisticId ? buildUserMessage() : message))
          }),
          selectedStoredSessionIdRef.current
        )

      const dropOptimistic = (sid: null | string) => {
        if (!sid) {
          setMessages(current => current.filter(m => m.id !== optimisticId))

          return
        }

        updateSessionState(
          sid,
          state => ({
            ...state,
            messages: state.messages.filter(m => m.id !== optimisticId),
            busy: false,
            awaitingResponse: false,
            pendingBranchGroup: null
          }),
          selectedStoredSessionIdRef.current
        )
      }

      setMutableRef(busyRef, true)
      setBusy(true)
      setAwaitingResponse(true)
      clearNotifications()

      let sessionId: null | string = activeSessionId

      if (sessionId) {
        seedOptimistic(sessionId)
      } else {
        setMessages(current => [...current, buildUserMessage()])
      }

      if (!sessionId) {
        try {
          sessionId = await createBackendSessionForSend(visibleText)
        } catch (err) {
          dropOptimistic(null)
          releaseBusy()
          notifyError(err, copy.sessionUnavailable)

          return false
        }

        if (!sessionId) {
          dropOptimistic(null)
          releaseBusy()
          notify({ kind: 'error', title: copy.sessionUnavailable, message: copy.createSessionFailed })

          return false
        }

        seedOptimistic(sessionId)
      }

      try {
        const syncedAttachments = await syncAttachmentsForSubmit(sessionId, attachments, {
          updateComposerAttachments: usingComposerAttachments
        })

        // Rewrite the optimistic message + prompt text with the synced refs so
        // the gateway receives @file: paths that resolve in its workspace.
        attachmentRefs = syncedAttachments.map(attachmentDisplayText).filter((r): r is string => Boolean(r))
        rewriteOptimistic(sessionId)
        const text = buildContextText(syncedAttachments)

        // On sleep/wake the gateway's in-memory session may have been cleared
        // while the desktop app still holds the old session ID. Detect this,
        // resume the stored session to re-register it, and retry once.
        let submitErr: unknown = null

        const buildPayload = (sid: string): PromptSubmitPayload => buildPromptPayload(sid, text, syncedAttachments)

        try {
          await requestGateway('prompt.submit', buildPayload(sessionId))
        } catch (firstErr) {
          const firstMsg = firstErr instanceof Error ? firstErr.message : String(firstErr)

          if (/session not found/i.test(firstMsg) && selectedStoredSessionIdRef.current) {
            // Re-register the session in the gateway and get a fresh live ID.
            const resumed = await requestGateway<{ session_id: string }>('session.resume', {
              session_id: selectedStoredSessionIdRef.current
            })

            const recoveredId = resumed?.session_id

            if (recoveredId) {
              activeSessionIdRef.current = recoveredId
              await requestGateway('prompt.submit', buildPayload(recoveredId))
            } else {
              submitErr = firstErr
            }
          } else {
            submitErr = firstErr
          }
        }

        if (submitErr !== null) {
          throw submitErr
        }

        if (usingComposerAttachments) {
          clearComposerAttachments()
        }

        return true
      } catch (err) {
        const message = inlineErrorMessage(err, copy.promptFailed)

        releaseBusy()
        updateSessionState(sessionId, state => ({
          ...state,
          messages: [
            ...state.messages,
            {
              id: `assistant-error-${Date.now()}`,
              role: 'assistant',
              parts: [],
              error: message || copy.promptFailed,
              branchGroupId: state.pendingBranchGroup ?? undefined
            }
          ],
          busy: false,
          awaitingResponse: false,
          pendingBranchGroup: null,
          sawAssistantPayload: true
        }))

        if (isProviderSetupError(err)) {
          notify({ kind: 'error', title: 'Provider setup required', message: copy.providerCredentialRequired })

          return false
        }

        notifyError(err, copy.promptFailed)

        return false
      }
    },
    [
      activeSessionId,
      activeSessionIdRef,
      busyRef,
      copy,
      createBackendSessionForSend,
      requestGateway,
      selectedStoredSessionIdRef,
      syncAttachmentsForSubmit,
      updateSessionState
    ]
  )

  const executeSlashCommand = useCallback(
    async (rawCommand: string, options?: { sessionId?: string; recordInput?: boolean }) => {
      const runSlash = async (commandText: string, sessionHint?: string, recordInput = true): Promise<void> => {
        const command = commandText.trim()
        const { name, arg } = parseSlashCommand(command)
        const normalizedName = name.toLowerCase()

        if (!name) {
          const sessionId = sessionHint || activeSessionIdRef.current || (await createBackendSessionForSend())

          if (sessionId) {
            appendSessionTextMessage(sessionId, 'system', copy.emptySlashCommand)
          }

          return
        }

        if (normalizedName === 'new' || normalizedName === 'reset') {
          startFreshSessionDraft()

          return
        }

        if (normalizedName === 'branch' || normalizedName === 'fork') {
          await branchCurrentSession()

          return
        }

        // /yolo maps to the status-bar YOLO control — a per-session approval
        // bypass, same scope as the TUI's Shift+Tab. With no session yet we arm
        // it locally; the session-create path applies it on the first message.
        if (normalizedName === 'yolo') {
          const sid = sessionHint || activeSessionIdRef.current
          const next = !$yoloActive.get()

          if (!sid) {
            setYoloActive(next)
            notify({ kind: 'success', message: next ? copy.yoloArmed : copy.yoloOff })

            return
          }

          try {
            const active = await setSessionYolo(requestGateway, sid, next)
            appendSessionTextMessage(sid, 'system', copy.yoloSystem(active))
          } catch {
            notify({ kind: 'error', title: copy.yoloTitle, message: copy.yoloToggleFailed })
          }

          return
        }

        // /model opens the desktop model picker overlay — the same full
        // provider+model picker reachable from the status-bar model button —
        // instead of the headless prompt_toolkit modal the slash worker can't
        // render. With explicit args (`/model <name> [--provider ...]`) run the
        // switch directly through slash.exec so power users can still type it.
        if (isModelPickerCommand(`/${normalizedName}`)) {
          if (!arg.trim()) {
            setModelPickerOpen(true)

            return
          }

          const sid = sessionHint || activeSessionIdRef.current || (await createBackendSessionForSend())

          if (!sid) {
            notify({ kind: 'error', title: 'Session unavailable', message: 'Could not create a new session' })

            return
          }

          try {
            const result = await requestGateway<SlashExecResponse>('slash.exec', {
              session_id: sid,
              command: command.replace(/^\/+/, '')
            })

            const body = result?.output || `/${name}: model switched`
            appendSessionTextMessage(sid, 'system', recordInput ? slashStatusText(command, body) : body)
          } catch (err) {
            appendSessionTextMessage(sid, 'system', `error: ${inlineErrorMessage(err, 'Slash command failed')}`)
          }

          return
        }

        if (normalizedName === 'skin' && !sessionHint && !activeSessionIdRef.current) {
          notify({ kind: 'success', message: handleSkinCommand(arg) })

          return
        }

        const sessionId = sessionHint || activeSessionIdRef.current || (await createBackendSessionForSend())

        if (!sessionId) {
          notify({
            kind: 'error',
            title: copy.sessionUnavailable,
            message: copy.createSessionFailed
          })

          return
        }

        const renderSlashOutput = (text: string) =>
          appendSessionTextMessage(sessionId, 'system', recordInput ? slashStatusText(command, text) : text)

        // /title <name> renames the session. Route through the gateway's
        // `session.title` RPC — the same path the TUI uses — NOT the REST
        // renameSession endpoint and NOT the slash worker.
        //
        // Why not the slash worker: it's a separate ZastCLI subprocess whose
        // SQLite write to the shared state.db can silently fail (notably on
        // Windows), and it never refreshes the sidebar.
        //
        // A bare `/title` (no arg) still falls through to the worker to display
        // the current title.
        if (normalizedName === 'title' && arg) {
          try {
            const result = await requestGateway<SessionTitleResponse>('session.title', {
              session_id: sessionId,
              title: arg
            })

            const finalTitle = (result?.title || arg).trim()

            setSessions(prev => prev.map(s => (s.id === sessionId ? { ...s, title: finalTitle || null } : s)))
            await refreshSessions().catch(() => undefined)
            renderSlashOutput(finalTitle ? `Session title set: ${finalTitle}` : 'Session title cleared.')
          } catch (err) {
            renderSlashOutput(`error: ${inlineErrorMessage(err, 'Could not rename session')}`)
          }

          return
        }

        if (normalizedName === 'skin') {
          renderSlashOutput(handleSkinCommand(arg))

          return
        }

        if (name === 'help' || name === 'commands') {
          try {
            const catalog = await requestGateway<CommandsCatalogLike>('commands.catalog', { session_id: sessionId })

            renderSlashOutput(renderCommandsCatalog(catalog, copy))
          } catch (err) {
            renderSlashOutput(`error: ${inlineErrorMessage(err, 'Could not load command catalog')}`)
          }

          return
        }

        if (!isDesktopSlashCommand(name)) {
          renderSlashOutput(desktopSlashUnavailableMessage(name) || `/${name} is not available in the desktop app.`)

          return
        }

        try {
          const result = await requestGateway<SlashExecResponse>('slash.exec', {
            session_id: sessionId,
            command: command.replace(/^\/+/, '')
          })

          const body = result?.output || `/${name}: no output`
          renderSlashOutput(result?.warning ? `warning: ${result.warning}\n${body}` : body)

          return
        } catch {
          // Fall back to command.dispatch for skill/send/alias directives.
        }

        try {
          const dispatch = parseCommandDispatch(
            await requestGateway<unknown>('command.dispatch', {
              session_id: sessionId,
              name,
              arg
            })
          )

          if (!dispatch) {
            renderSlashOutput('error: invalid response: command.dispatch')

            return
          }

          if (dispatch.type === 'exec' || dispatch.type === 'plugin') {
            renderSlashOutput(dispatch.output ?? '(no output)')

            return
          }

          if (dispatch.type === 'alias') {
            await runSlash(`/${dispatch.target}${arg ? ` ${arg}` : ''}`, sessionId, false)

            return
          }

          const message = ('message' in dispatch ? dispatch.message : '')?.trim() ?? ''

          if (!message) {
            renderSlashOutput(
              `/${name}: ${dispatch.type === 'skill' ? 'skill payload missing message' : 'empty message'}`
            )

            return
          }

          if (dispatch.type === 'skill') {
            renderSlashOutput(`⚡ loading skill: ${dispatch.name}`)
          }

          if (busyRef.current) {
            renderSlashOutput('session busy — /interrupt the current turn before sending this command')

            return
          }

          await submitPromptText(message)
        } catch (err) {
          renderSlashOutput(`error: ${inlineErrorMessage(err, 'Slash command failed')}`)
        }
      }

      await runSlash(rawCommand, options?.sessionId, options?.recordInput ?? true)
    },
    [
      activeSessionIdRef,
      appendSessionTextMessage,
      branchCurrentSession,
      busyRef,
      copy,
      createBackendSessionForSend,
      handleSkinCommand,
      refreshSessions,
      requestGateway,
      startFreshSessionDraft,
      submitPromptText
    ]
  )

  const submitText = useCallback(
    async (rawText: string, options?: SubmitTextOptions) => {
      const visibleText = rawText.trim()
      const attachments = options?.attachments ?? $composerAttachments.get()

      if (!attachments.length && SLASH_COMMAND_RE.test(visibleText)) {
        triggerHaptic('selection')
        await executeSlashCommand(visibleText)

        return true
      }

      return await submitPromptText(rawText, options)
    },
    [executeSlashCommand, submitPromptText]
  )

  const transcribeVoiceAudio = useCallback(
    async (audio: Blob) => {
      if (!sttEnabled) {
        throw new Error(copy.sttDisabled)
      }

      const dataUrl = await blobToDataUrl(audio)
      const result = await transcribeAudio(dataUrl, audio.type)

      return result.transcript
    },
    [copy.sttDisabled, sttEnabled]
  )

  const cancelRun = useCallback(async () => {
    const sessionId = activeSessionId || activeSessionIdRef.current

    setAwaitingResponse(false)

    // Interrupting keeps whatever was already generated and just
    // stops — no "[interrupted]" marker. A pending/streaming message with no
    // body text is dropped entirely so we never leave an empty bubble behind.
    const finalizeMessages = (messages: ChatMessage[], streamId?: string | null) =>
      messages
        .filter(message => !((message.pending || message.id === streamId) && !chatMessageText(message).trim()))
        .map(message => (message.pending || message.id === streamId ? { ...message, pending: false } : message))

    if (!sessionId) {
      setMutableRef(busyRef, false)
      setBusy(false)
      setMessages(finalizeMessages($messages.get()))

      return
    }

    updateSessionState(sessionId, state => {
      const streamId = state.streamId

      const messages = finalizeMessages(state.messages, streamId)

      return {
        ...state,
        messages,
        busy: true,
        awaitingResponse: false,
        streamId: null,
        pendingBranchGroup: null,
        interrupted: true
      }
    })

    try {
      await requestGateway('session.interrupt', { session_id: sessionId })
    } catch (err) {
      setMutableRef(busyRef, false)
      setBusy(false)
      notifyError(err, copy.stopFailed)
    }

    // Fire-and-forget: signal Runner to abort in-flight tools. Not awaited
    // so a missing/offline Runner never blocks the UI state recovery above.
    void window.zastDesktop?.runnerDispatch?.('zast.cancel')
  }, [activeSessionId, activeSessionIdRef, busyRef, copy.stopFailed, requestGateway, updateSessionState])

  // Steer = nudge the live turn without interrupting: the gateway appends the
  // text to the next tool result so the model reads it on its next iteration
  // (desktop parity with `/steer`). Returns false on reject (no live tool
  // window) so the caller can fall back to queueing the words for the next turn.
  const steerPrompt = useCallback(
    async (rawText: string): Promise<boolean> => {
      const text = rawText.trim()
      const sessionId = activeSessionId || activeSessionIdRef.current

      if (!text || !sessionId) {
        return false
      }

      try {
        const result = await requestGateway<SessionSteerResponse>('session.steer', { session_id: sessionId, text })

        if (result?.status === 'queued') {
          triggerHaptic('submit')
          // Inline note (not a toast) so the nudge lives in the transcript next
          // to the turn it steered. The `steer:` prefix is rendered as a codicon
          // row by SystemMessage (see STEER_NOTE_RE), same style as slash output.
          appendSessionTextMessage(sessionId, 'system', `steer:${text}`)

          return true
        }
      } catch {
        // Swallow — caller queues the text so nothing is lost.
      }

      return false
    },
    [activeSessionId, activeSessionIdRef, appendSessionTextMessage, requestGateway]
  )

  const reloadFromMessage = useCallback(
    async (parentId: string | null) => {
      if (!activeSessionId || $busy.get()) {
        return
      }

      const messages = $messages.get()
      const parentIndex = parentId ? messages.findIndex(message => message.id === parentId) : messages.length - 1

      const userIndex =
        parentIndex >= 0
          ? [...messages.slice(0, parentIndex + 1)].reverse().findIndex(message => message.role === 'user')
          : -1

      if (userIndex < 0) {
        return
      }

      const absoluteUserIndex = parentIndex - userIndex
      const userMessage = messages[absoluteUserIndex]
      const userText = userMessage ? chatMessageText(userMessage).trim() : ''

      if (!userText) {
        return
      }

      const targetAssistant =
        parentId && messages[parentIndex]?.role === 'assistant'
          ? messages[parentIndex]
          : messages.slice(absoluteUserIndex + 1).find(message => message.role === 'assistant')

      const branchGroupId = targetAssistant?.branchGroupId ?? branchGroupForUser(userMessage)
      const truncateBeforeUserOrdinal = visibleUserOrdinal(messages, absoluteUserIndex)

      clearNotifications()
      updateSessionState(activeSessionId, state => {
        const nextUserIndex = state.messages.findIndex(
          (message, index) => index > absoluteUserIndex && message.role === 'user'
        )

        const end = nextUserIndex < 0 ? state.messages.length : nextUserIndex

        return {
          ...state,
          busy: true,
          awaitingResponse: true,
          pendingBranchGroup: branchGroupId,
          sawAssistantPayload: false,
          interrupted: false,
          messages: [
            ...state.messages.slice(0, absoluteUserIndex + 1),
            ...state.messages
              .slice(absoluteUserIndex + 1, end)
              .map(message => (message.role === 'assistant' ? { ...message, branchGroupId, hidden: true } : message))
          ]
        }
      })

      try {
        await requestGateway('prompt.submit', {
          session_id: activeSessionId,
          text: userText,
          truncate_before_user_ordinal: truncateBeforeUserOrdinal
        })
      } catch (err) {
        updateSessionState(activeSessionId, state => ({
          ...state,
          busy: false,
          awaitingResponse: false
        }))
        notifyError(err, copy.regenerateFailed)
      }
    },
    [activeSessionId, copy.regenerateFailed, requestGateway, updateSessionState]
  )

  const editMessage = useCallback(
    async (edited: AppendMessage) => {
      const sessionId = activeSessionId || activeSessionIdRef.current
      const sourceId = edited.sourceId || edited.parentId
      const text = appendText(edited)

      if (!sessionId || !sourceId || !text || edited.role !== 'user' || $busy.get()) {
        return
      }

      const messages = $messages.get()
      const sourceIndex = messages.findIndex(m => m.id === sourceId)
      const source = messages[sourceIndex]

      if (!source || source.role !== 'user' || chatMessageText(source).trim() === text) {
        return
      }

      // Failed turn: optimistic user msg never reached the gateway, so truncating
      // by ordinal would 422. Submit as a plain resend instead.
      const nextMessage = messages[sourceIndex + 1]
      const isFailedTurn = nextMessage?.role === 'assistant' && Boolean(nextMessage.error)
      const editedMessage: ChatMessage = { ...source, parts: [textPart(text)] }

      clearNotifications()
      setMutableRef(busyRef, true)
      setBusy(true)
      setAwaitingResponse(true)
      updateSessionState(sessionId, state => ({
        ...state,
        busy: true,
        awaitingResponse: true,
        pendingBranchGroup: null,
        sawAssistantPayload: false,
        interrupted: false,
        messages: [...state.messages.slice(0, sourceIndex), editedMessage]
      }))

      const submit = (truncateOrdinal?: number) =>
        requestGateway('prompt.submit', {
          session_id: sessionId,
          text,
          ...(truncateOrdinal !== undefined && { truncate_before_user_ordinal: truncateOrdinal })
        })

      // Backend raises ``INVALID_PARAMS`` (-32602) for stale
      // ``truncate_before_user_ordinal`` (see ``routers/chat.py:172``); the
      // message regex is retained as a guard so we don't accidentally retry
      // any other InvalidParams failure.
      const isStaleTargetError = (err: unknown) =>
        err instanceof ZastRpcError &&
        err.code === ZastRpcErrorCode.InvalidParams &&
        /no longer in session history|not in session history/i.test(err.message)

      try {
        await submit(isFailedTurn ? undefined : visibleUserOrdinal(messages, sourceIndex))
      } catch (err) {
        let surfaced = err

        if (!isFailedTurn && isStaleTargetError(err)) {
          try {
            await submit()

            return
          } catch (retryErr) {
            surfaced = retryErr
          }
        }

        setMutableRef(busyRef, false)
        setBusy(false)
        setAwaitingResponse(false)
        updateSessionState(sessionId, state => ({ ...state, busy: false, awaitingResponse: false }))
        notifyError(surfaced, copy.editFailed)
      }
    },
    [activeSessionId, activeSessionIdRef, busyRef, copy.editFailed, requestGateway, updateSessionState]
  )

  const handleThreadMessagesChange = useCallback(
    (nextMessages: readonly ThreadMessage[]) => {
      const visibleIds = new Set(nextMessages.map(m => m.id))
      const sessionId = activeSessionIdRef.current

      if (!sessionId) {
        return
      }

      updateSessionState(sessionId, state => {
        let changed = false

        const messages = state.messages.map(message => {
          if (message.role !== 'assistant' || !message.branchGroupId) {
            return message
          }

          const hidden = !visibleIds.has(message.id)

          if (message.hidden === hidden) {
            return message
          }

          changed = true

          return { ...message, hidden }
        })

        return changed ? { ...state, messages } : state
      })
    },
    [activeSessionIdRef, updateSessionState]
  )

  return {
    cancelRun,
    editMessage,
    handleThreadMessagesChange,
    reloadFromMessage,
    steerPrompt,
    submitText,
    transcribeVoiceAudio
  }
}
