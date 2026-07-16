import { useStore } from '@nanostores/react'
import { lazy, Suspense, useCallback, useEffect, useRef } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom'

import { BootFailureOverlay } from '@/components/boot-failure-overlay'
import { GatewayConnectingOverlay } from '@/components/gateway-connecting-overlay'
import { Pane, PaneMain } from '@/components/pane-shell'
import { useMediaQuery } from '@/hooks/use-media-query'
import { useSkinCommand } from '@/themes/use-skin-command'

import { formatRefValue } from '../components/assistant-ui/directive-text'
import { preserveLocalAssistantErrors, toChatMessages } from '../lib/chat-messages'
import {
  $panesFlipped,
  $pinnedSessionIds,
  $sessionsLimit,
  bumpSessionsLimit,
  FILE_BROWSER_DEFAULT_WIDTH,
  FILE_BROWSER_MAX_WIDTH,
  FILE_BROWSER_MIN_WIDTH,
  pinSession,
  setSidebarOverlayMounted,
  SIDEBAR_DEFAULT_WIDTH,
  SIDEBAR_MAX_WIDTH,
  unpinSession
} from '../store/layout'
import { $filePreviewTarget, $previewTarget, closeActiveRightRailTab } from '../store/preview'
import {
  $activeSessionId,
  $currentCwd,
  $freshDraftReady,
  $gatewayState,
  $selectedStoredSessionId,
  $sessions,
  $showSubagentsInSidebar,
  $workingSessionIds,
  getRecentlySettledSessionIds,
  mergeSessionPage,
  sessionPinId,
  setAwaitingResponse,
  setBusy,
  setCurrentBranch,
  setCurrentCwd,
  setMessages,
  setSessions,
  setSessionsLoading,
  setSessionsTotal
} from '../store/session'
import { getSessionMessages, listSessions } from '../zast'

import { ChatView } from './chat'
import { useComposerActions } from './chat/hooks/use-composer-actions'
import {
  ChatPreviewRail,
  PREVIEW_RAIL_MAX_WIDTH,
  PREVIEW_RAIL_MIN_WIDTH,
  PREVIEW_RAIL_PANE_WIDTH
} from './chat/right-rail'
import { ChatSidebar } from './chat/sidebar'
import { CommandPalette } from './command-palette'
import { useGatewayBoot } from './gateway/hooks/use-gateway-boot'
import { useGatewayRequest } from './gateway/hooks/use-gateway-request'
import { useKeybinds } from './hooks/use-keybinds'
import { SIDEBAR_COLLAPSE_MEDIA_QUERY } from './layout-constants'
import { RightSidebarPane } from './right-sidebar'
import { $terminalTakeover } from './right-sidebar/store'
import { PersistentTerminal, TerminalSlot } from './right-sidebar/terminal/persistent'
import { NEW_CHAT_ROUTE, routeSessionId, sessionRoute } from './routes'
import { useContextSuggestions } from './session/hooks/use-context-suggestions'
import { useCwdActions } from './session/hooks/use-cwd-actions'
import { useMessageStream } from './session/hooks/use-message-stream'
import { usePreviewRouting } from './session/hooks/use-preview-routing'
import { usePromptActions } from './session/hooks/use-prompt-actions'
import { useRouteResume } from './session/hooks/use-route-resume'
import { useSessionActions } from './session/hooks/use-session-actions'
import { useSessionStateCache } from './session/hooks/use-session-state-cache'
import { useZastConfig } from './session/hooks/use-zast-config'
import { AppShell } from './shell/app-shell'
import { useOverlayRouting } from './shell/hooks/use-overlay-routing'
import { useStatusSnapshot } from './shell/hooks/use-status-snapshot'
import { useStatusbarItems } from './shell/hooks/use-statusbar-items'
import type { StatusbarItem } from './shell/statusbar-controls'
import type { TitlebarTool } from './shell/titlebar-controls'
import { UpdateToast } from './shell/update-toast'
import { useGroupRegistry } from './shell/use-group-registry'

const ArtifactsView = lazy(async () => ({ default: (await import('./artifacts')).ArtifactsView }))
const SettingsView = lazy(async () => ({ default: (await import('./settings')).SettingsView }))
const InsightsView = lazy(async () => ({ default: (await import('./insights')).InsightsView }))

// Rows a session refresh must preserve even if the aggregator omits them:
// in-flight first turns (message_count 0), pinned rows aged off the page, the
// actively-viewed chat (its "working" flag clears a beat before the aggregator
// sees the persisted row), and sessions whose turn just settled (same race, but
// for a chat the user has already navigated away from).
function sessionsToKeep(): Set<string> {
  const keep = new Set<string>([
    ...$workingSessionIds.get(),
    ...$pinnedSessionIds.get(),
    ...getRecentlySettledSessionIds()
  ])

  const active = $selectedStoredSessionId.get()

  if (active) {
    keep.add(active)
  }

  return keep
}

export function DesktopController() {
  const location = useLocation()
  const navigate = useNavigate()

  const busyRef = useRef(false)
  const creatingSessionRef = useRef(false)
  const refreshSessionsRequestRef = useRef(0)

  const gatewayState = useStore($gatewayState)
  const activeSessionId = useStore($activeSessionId)
  const currentCwd = useStore($currentCwd)
  const freshDraftReady = useStore($freshDraftReady)
  const filePreviewTarget = useStore($filePreviewTarget)
  const previewTarget = useStore($previewTarget)
  const selectedStoredSessionId = useStore($selectedStoredSessionId)
  const terminalTakeover = useStore($terminalTakeover)
  const panesFlipped = useStore($panesFlipped)
  // Below SIDEBAR_COLLAPSE_BREAKPOINT_PX there's no room for a docked rail —
  // collapse both sidebars (without touching their stored open state) so the
  // hover-reveal overlay becomes the way in. Restores once it's wide again.
  const narrowViewport = useMediaQuery(SIDEBAR_COLLAPSE_MEDIA_QUERY)

  const routedSessionId = routeSessionId(location.pathname)
  const routeToken = `${location.pathname}:${location.search}:${location.hash}`
  const routeTokenRef = useRef(routeToken)
  routeTokenRef.current = routeToken
  const getRouteToken = useCallback(() => routeTokenRef.current, [])

  const { chatOpen, closeOverlayToPreviousRoute, currentView, settingsOpen } = useOverlayRouting()

  const terminalTakeoverActive = chatOpen && terminalTakeover

  const titlebarToolGroups = useGroupRegistry<TitlebarTool>()
  const statusbarItemGroups = useGroupRegistry<StatusbarItem>()
  const setTitlebarToolGroup = titlebarToolGroups.set
  const setStatusbarItemGroup = statusbarItemGroups.set

  const {
    activeSessionIdRef,
    ensureSessionState,
    selectedStoredSessionIdRef,
    sessionStateBySessionIdRef,
    syncSessionStateToView,
    updateSessionState
  } = useSessionStateCache({
    activeSessionId,
    busyRef,
    selectedStoredSessionId,
    setAwaitingResponse,
    setBusy,
    setMessages
  })

  const { connectionRef, gatewayRef, requestGateway } = useGatewayRequest()

  useEffect(() => {
    window.zastDesktop?.setPreviewShortcutActive?.(Boolean(chatOpen && (filePreviewTarget || previewTarget)))
  }, [chatOpen, filePreviewTarget, previewTarget])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!$filePreviewTarget.get() && !$previewTarget.get()) {
        return
      }

      if ((event.metaKey || event.ctrlKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === 'w') {
        event.preventDefault()
        event.stopPropagation()
        closeActiveRightRailTab()
      }
    }

    const unsubscribe = window.zastDesktop?.onClosePreviewRequested?.(closeActiveRightRailTab)

    window.addEventListener('keydown', onKeyDown, { capture: true })

    return () => {
      unsubscribe?.()
      window.removeEventListener('keydown', onKeyDown, { capture: true })
    }
  }, [])

  const refreshSessions = useCallback(async () => {
    const requestId = refreshSessionsRequestRef.current + 1
    refreshSessionsRequestRef.current = requestId
    setSessionsLoading(true)

    try {
      const limit = $sessionsLimit.get()

      const result = await listSessions(limit, 1, 'exclude', 'recent', $showSubagentsInSidebar.get())

      if (refreshSessionsRequestRef.current === requestId) {
        setSessions(prev => mergeSessionPage(prev, result.sessions, sessionsToKeep()))
        setSessionsTotal(typeof result.total === 'number' ? result.total : result.sessions.length)
      }
    } finally {
      if (refreshSessionsRequestRef.current === requestId) {
        setSessionsLoading(false)
      }
    }
  }, [])

  const loadMoreSessions = useCallback(() => {
    bumpSessionsLimit()
    void refreshSessions()
  }, [refreshSessions])

  const toggleSelectedPin = useCallback(() => {
    const sessionId = $selectedStoredSessionId.get()

    if (!sessionId) {
      return
    }

    // Pin on the durable lineage-root id so the pin survives auto-compression.
    const session = $sessions.get().find(s => s.id === sessionId || s._lineage_root_id === sessionId)
    const pinId = session ? sessionPinId(session) : sessionId

    if ($pinnedSessionIds.get().includes(pinId)) {
      unpinSession(pinId)
    } else {
      pinSession(pinId)
    }
  }, [])

  const { inferenceStatus, statusSnapshot } = useStatusSnapshot(gatewayState, requestGateway)

  const updateActiveSessionRuntimeInfo = useCallback(
    (info: { branch?: string; cwd?: string }) => {
      const sessionId = activeSessionIdRef.current

      if (!sessionId) {
        return
      }

      updateSessionState(sessionId, state => ({
        ...state,
        branch: info.branch ?? state.branch,
        cwd: info.cwd ?? state.cwd
      }))
    },
    [activeSessionIdRef, updateSessionState]
  )

  const { changeSessionCwd, refreshProjectBranch } = useCwdActions({
    activeSessionId,
    activeSessionIdRef,
    onSessionRuntimeInfo: updateActiveSessionRuntimeInfo,
    requestGateway
  })

  const { refreshZastConfig, sttEnabled, voiceMaxRecordingSeconds } = useZastConfig({
    activeSessionIdRef,
    refreshProjectBranch
  })

  useContextSuggestions({
    activeSessionId,
    activeSessionIdRef,
    currentCwd,
    gatewayState,
    requestGateway
  })

  const hydrateFromStoredSession = useCallback(
    async (
      attempts = 1,
      storedSessionId = selectedStoredSessionIdRef.current,
      runtimeSessionId = activeSessionIdRef.current
    ) => {
      if (!storedSessionId || !runtimeSessionId) {
        return
      }

      for (let index = 0; index < Math.max(1, attempts); index += 1) {
        try {
          const latest = await getSessionMessages(storedSessionId)
          updateSessionState(
            runtimeSessionId,
            state => ({
              ...state,
              messages: preserveLocalAssistantErrors(toChatMessages(latest.messages), state.messages)
            }),
            storedSessionId
          )

          return
        } catch {
          // Best-effort fallback when live stream payloads are empty.
        }

        if (index < attempts - 1) {
          await new Promise(resolve => window.setTimeout(resolve, 250))
        }
      }
    },
    [activeSessionIdRef, selectedStoredSessionIdRef, updateSessionState]
  )

  const { handleGatewayEvent } = useMessageStream({
    activeSessionIdRef,
    hydrateFromStoredSession,
    refreshZastConfig,
    refreshSessions,
    updateSessionState
  })

  const { handleDesktopGatewayEvent } = usePreviewRouting({
    activeSessionIdRef,
    baseHandleGatewayEvent: handleGatewayEvent,
    currentCwd,
    currentView,
    requestGateway,
    routedSessionId,
    selectedStoredSessionId
  })

  const {
    archiveSession,
    branchCurrentSession,
    createBackendSessionForSend,
    openSettings,
    removeSession,
    resumeSession,
    selectSidebarItem,
    startFreshSessionDraft
  } = useSessionActions({
    activeSessionId,
    activeSessionIdRef,
    busyRef,
    creatingSessionRef,
    ensureSessionState,
    getRouteToken,
    navigate,
    requestGateway,
    selectedStoredSessionId,
    selectedStoredSessionIdRef,
    sessionStateBySessionIdRef,
    syncSessionStateToView,
    updateSessionState
  })

  useKeybinds({
    startFreshSession: startFreshSessionDraft,
    toggleSelectedPin
  })

  const composer = useComposerActions({
    activeSessionId,
    currentCwd,
    requestGateway
  })

  const branchInNewChat = useCallback(
    async (messageId?: string) => {
      const branched = await branchCurrentSession(messageId)

      if (branched) {
        await refreshSessions().catch(() => undefined)
      }

      return branched
    },
    [branchCurrentSession, refreshSessions]
  )

  const startSessionInWorkspace = useCallback(
    (path: null | string) => {
      startFreshSessionDraft()

      const target = path?.trim()

      if (!target) {
        return
      }

      // The next message creates the backend session in $currentCwd, so seed
      // it (and the branch) from the workspace the user clicked the + on.
      setCurrentCwd(target)
      void requestGateway<{ branch?: string; cwd?: string }>('config.get', { key: 'project', cwd: target })
        .then(info => {
          setCurrentCwd(info.cwd || target)
          setCurrentBranch(info.branch || '')
        })
        .catch(() => undefined)
    },
    [requestGateway, startFreshSessionDraft]
  )

  const handleSkinCommand = useSkinCommand()

  const {
    cancelRun,
    editMessage,
    handleThreadMessagesChange,
    reloadFromMessage,
    steerPrompt,
    submitText,
    transcribeVoiceAudio
  } = usePromptActions({
    activeSessionId,
    activeSessionIdRef,
    branchCurrentSession: branchInNewChat,
    busyRef,
    createBackendSessionForSend,
    handleSkinCommand,
    refreshSessions,
    requestGateway,
    selectedStoredSessionIdRef,
    startFreshSessionDraft,
    sttEnabled,
    updateSessionState
  })

  useGatewayBoot({
    handleGatewayEvent: handleDesktopGatewayEvent,
    onConnectionReady: c => {
      connectionRef.current = c
    },
    onGatewayReady: g => {
      gatewayRef.current = g
    },
    refreshZastConfig,
    refreshSessions
  })

  useEffect(() => {
    if (gatewayState === 'open') {
      void refreshSessions().catch(() => undefined)
    }
  }, [gatewayState, refreshSessions])

  useRouteResume({
    activeSessionId,
    activeSessionIdRef,
    creatingSessionRef,
    currentView,
    freshDraftReady,
    gatewayState,
    locationPathname: location.pathname,
    resumeSession,
    routedSessionId,
    selectedStoredSessionId,
    selectedStoredSessionIdRef,
    startFreshSessionDraft
  })

  const { leftStatusbarItems, statusbarItems } = useStatusbarItems({
    extraLeftItems: statusbarItemGroups.flat.left,
    extraRightItems: statusbarItemGroups.flat.right,
    gatewayState,
    inferenceStatus,
    freshDraftReady,
    requestGateway,
    resumeSession,
    statusSnapshot
  })

  const sidebar = (
    <ChatSidebar
      currentView={currentView}
      onArchiveSession={sessionId => void archiveSession(sessionId)}
      onDeleteSession={sessionId => void removeSession(sessionId)}
      onLoadMoreSessions={loadMoreSessions}
      onNavigate={selectSidebarItem}
      onNewSessionInWorkspace={startSessionInWorkspace}
      onResumeSession={sessionId => navigate(sessionRoute(sessionId))}
    />
  )

  const overlays = (
    <>
      {/* One PTY-backed terminal mounted forever; <TerminalSlot /> placeholders
          decide where it shows. Toggling fullscreen never rebuilds the shell. */}
      <PersistentTerminal cwd={currentCwd} onAddSelectionToChat={composer.addTerminalSelectionAttachment} />
      <GatewayConnectingOverlay />
      <BootFailureOverlay />
      <UpdateToast />
      <CommandPalette />

      {settingsOpen && (
        <Suspense fallback={null}>
          <SettingsView
            gateway={gatewayRef.current}
            onClose={closeOverlayToPreviousRoute}
            onConfigSaved={() => {
              // Best-effort config re-read into the atom; refreshSessions still
              // runs if the GET fails so the sidebar reflects the user's
              // saved preference (the PUT already committed server-side).
              void refreshZastConfig().finally(() => refreshSessions())
            }}
          />
        </Suspense>
      )}
    </>
  )

  const chatView = (
    <ChatView
      gateway={gatewayRef.current}
      maxVoiceRecordingSeconds={voiceMaxRecordingSeconds}
      onAddContextRef={composer.addContextRefAttachment}
      onAddUrl={url => composer.addContextRefAttachment(`@url:${formatRefValue(url)}`, url)}
      onAttachDroppedItems={composer.attachDroppedItems}
      onAttachImageBlob={composer.attachImageBlob}
      onBranchInNewChat={branchInNewChat}
      onCancel={cancelRun}
      onDeleteSelectedSession={() => {
        if (selectedStoredSessionId) {
          void removeSession(selectedStoredSessionId)
        }
      }}
      onEdit={editMessage}
      onPasteClipboardImage={() => void composer.pasteClipboardImage()}
      onPickFiles={() => void composer.pickContextPaths('file')}
      onPickFolders={() => void composer.pickContextPaths('folder')}
      onPickImages={() => void composer.pickImages()}
      onReload={reloadFromMessage}
      onRemoveAttachment={id => void composer.removeAttachment(id)}
      onSteer={steerPrompt}
      onSubmit={submitText}
      onThreadMessagesChange={handleThreadMessagesChange}
      onToggleSelectedPin={toggleSelectedPin}
      onTranscribeAudio={transcribeVoiceAudio}
    />
  )

  const takeoverTerminalView = (
    <div className="relative flex h-full min-h-0 min-w-0 flex-col overflow-hidden bg-(--ui-chat-surface-background) pt-(--titlebar-height)">
      <TerminalSlot />
    </div>
  )

  const sidebarSide = panesFlipped ? 'right' : 'left'
  const railSide = panesFlipped ? 'left' : 'right'

  const previewPane = (
    <Pane
      disabled={!chatOpen || (!previewTarget && !filePreviewTarget)}
      id="preview"
      key="preview"
      maxWidth={PREVIEW_RAIL_MAX_WIDTH}
      minWidth={PREVIEW_RAIL_MIN_WIDTH}
      resizable
      side={railSide}
      width={PREVIEW_RAIL_PANE_WIDTH}
    >
      {chatOpen ? <ChatPreviewRail setTitlebarToolGroup={setTitlebarToolGroup} /> : null}
    </Pane>
  )

  const fileBrowserPane = (
    <Pane
      defaultOpen={false}
      disabled={!chatOpen}
      forceCollapsed={narrowViewport}
      hoverReveal
      id="file-browser"
      key="file-browser"
      maxWidth={FILE_BROWSER_MAX_WIDTH}
      minWidth={FILE_BROWSER_MIN_WIDTH}
      resizable
      side={railSide}
      width={FILE_BROWSER_DEFAULT_WIDTH}
    >
      <RightSidebarPane
        onActivateFile={composer.attachContextFilePath}
        onActivateFolder={composer.attachContextFolderPath}
        onChangeCwd={changeSessionCwd}
      />
    </Pane>
  )

  return (
    <AppShell
      leftStatusbarItems={leftStatusbarItems}
      leftTitlebarTools={titlebarToolGroups.flat.left}
      onOpenSettings={openSettings}
      overlays={overlays}
      statusbarItems={statusbarItems}
      titlebarTools={titlebarToolGroups.flat.right}
    >
      <Pane
        disabled={terminalTakeoverActive}
        forceCollapsed={narrowViewport}
        hoverReveal
        id="chat-sidebar"
        maxWidth={SIDEBAR_MAX_WIDTH}
        minWidth={SIDEBAR_DEFAULT_WIDTH}
        onOverlayActiveChange={setSidebarOverlayMounted}
        resizable
        side={sidebarSide}
        width={`${SIDEBAR_DEFAULT_WIDTH}px`}
      >
        {sidebar}
      </Pane>
      <PaneMain>
        <Routes>
          <Route element={terminalTakeoverActive ? takeoverTerminalView : chatView} index />
          <Route element={terminalTakeoverActive ? takeoverTerminalView : chatView} path=":sessionId" />
          <Route
            element={
              <Suspense fallback={null}>
                <ArtifactsView setStatusbarItemGroup={setStatusbarItemGroup} />
              </Suspense>
            }
            path="artifacts"
          />
          <Route
            element={
              <Suspense fallback={null}>
                <InsightsView />
              </Suspense>
            }
            path="insights"
          />
          <Route element={null} path="settings" />
          <Route element={null} path="command-center" />
          <Route element={<Navigate replace to={NEW_CHAT_ROUTE} />} path="new" />
          <Route element={<LegacySessionRedirect />} path="sessions/:sessionId" />
          <Route element={<Navigate replace to={NEW_CHAT_ROUTE} />} path="*" />
        </Routes>
      </PaneMain>
      {/*
        Order within a side maps to column order. Default (rail on the right):
        main | preview | file-browser. Flipped (rail on the left): mirror it to
        file-browser | preview | main so preview stays adjacent to the chat.
      */}
      {panesFlipped ? fileBrowserPane : previewPane}
      {panesFlipped ? previewPane : fileBrowserPane}
    </AppShell>
  )
}

function LegacySessionRedirect() {
  const { sessionId } = useParams()

  return <Navigate replace to={sessionId ? sessionRoute(sessionId) : NEW_CHAT_ROUTE} />
}
