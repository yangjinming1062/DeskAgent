import { useStore } from '@nanostores/react'
import { type MutableRefObject, useCallback, useEffect } from 'react'

import { gatewayEventCompletedFileDiff } from '@/lib/gateway-events'
import {
  $previewTarget,
  $sessionPreviewRegistry,
  getSessionPreviewRecord,
  requestPreviewReload,
  setPreviewTarget,
  setSessionPreviewTarget
} from '@/store/preview'
import { $currentCwd } from '@/store/session'
import type { RpcEvent } from '@/types/zast'

type EventHandler = (event: RpcEvent) => void

interface PreviewRoutingOptions {
  activeSessionIdRef: MutableRefObject<string | null>
  baseHandleGatewayEvent: EventHandler
  currentCwd: string
  currentView: string
  requestGateway: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>
  routedSessionId: string | null
  selectedStoredSessionId: string | null
}

function asRecord(payload: unknown): Record<string, unknown> {
  return payload && typeof payload === 'object' ? (payload as Record<string, unknown>) : {}
}

function activePreviewSessionId(
  activeSessionIdRef: MutableRefObject<string | null>,
  routedSessionId: string | null,
  selectedStoredSessionId: string | null
): string {
  return selectedStoredSessionId || routedSessionId || activeSessionIdRef.current || ''
}

function looksLikePreviewTarget(value: string): boolean {
  return /^https?:\/\//i.test(value) || /^file:\/\//i.test(value) || /^(?:\/|\.{1,2}\/|~\/).+/.test(value)
}

function stripAnsi(value: string): string {
  return value.replace(new RegExp(`${String.fromCharCode(27)}\\[[0-9;]*m`, 'g'), '')
}

function htmlPathFromInlineDiff(value: string): string {
  const cleaned = stripAnsi(value).replace(/^\s*┊\s*review diff\s*\n/i, '')

  for (const match of cleaned.matchAll(/(?:^|\s)(?:[ab]\/)?([^\s]+\.html?)(?=\s|$)/gi)) {
    const candidate = match[1]?.trim()

    if (candidate) {
      return candidate
    }
  }

  return ''
}

function structuredPreviewCandidate(payload: unknown): string {
  const record = asRecord(payload)
  const fields = ['url', 'target', 'path', 'file', 'filepath', 'preview']

  for (const field of fields) {
    const value = record[field]

    if (typeof value === 'string') {
      const target = value.trim()

      if (target && looksLikePreviewTarget(target)) {
        return target
      }
    }
  }

  const inlineDiff = record.inline_diff

  if (typeof inlineDiff === 'string') {
    return htmlPathFromInlineDiff(inlineDiff)
  }

  return ''
}

export function usePreviewRouting({
  activeSessionIdRef,
  baseHandleGatewayEvent,
  currentCwd,
  currentView,
  requestGateway,
  routedSessionId,
  selectedStoredSessionId
}: PreviewRoutingOptions) {
  const previewRegistry = useStore($sessionPreviewRegistry)
  const previewSessionId = activePreviewSessionId(activeSessionIdRef, routedSessionId, selectedStoredSessionId)

  useEffect(() => {
    if (currentView !== 'chat' || !previewSessionId) {
      setPreviewTarget(null)

      return
    }

    const record = getSessionPreviewRecord(previewSessionId)

    setPreviewTarget(record?.normalized ?? null)
  }, [currentView, previewRegistry, previewSessionId])

  const registerStructuredPreview = useCallback(
    async (event: RpcEvent) => {
      if (
        event.session_id &&
        event.session_id !== activeSessionIdRef.current &&
        event.session_id !== previewSessionId
      ) {
        return
      }

      if (!event.type.startsWith('tool.')) {
        return
      }

      if (!previewSessionId) {
        return
      }

      const candidate = structuredPreviewCandidate(event.payload)

      if (!candidate) {
        return
      }

      const desktop = window.zastDesktop

      if (!desktop?.normalizePreviewTarget) {
        return
      }

      const sessionId = previewSessionId
      const cwd = currentCwd || ''
      const target = await desktop.normalizePreviewTarget(candidate, cwd || undefined).catch(() => null)

      if (
        !target ||
        sessionId !== activePreviewSessionId(activeSessionIdRef, routedSessionId, selectedStoredSessionId) ||
        $currentCwd.get() !== cwd
      ) {
        return
      }

      setSessionPreviewTarget(sessionId, target, 'tool-result', candidate)
    },
    [activeSessionIdRef, currentCwd, previewSessionId, routedSessionId, selectedStoredSessionId]
  )

  const handleDesktopGatewayEvent = useCallback<EventHandler>(
    event => {
      baseHandleGatewayEvent(event)

      if (event.session_id && event.session_id !== activeSessionIdRef.current) {
        return
      }

      void registerStructuredPreview(event)

      if ($previewTarget.get()?.kind === 'url' && gatewayEventCompletedFileDiff(event)) {
        requestPreviewReload()
      }
    },
    [activeSessionIdRef, baseHandleGatewayEvent, registerStructuredPreview]
  )

  return { handleDesktopGatewayEvent }
}
