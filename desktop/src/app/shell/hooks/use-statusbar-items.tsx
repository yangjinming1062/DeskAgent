import { useStore } from '@nanostores/react'
import { useCallback, useMemo } from 'react'

import { GatewayMenuPanel } from '@/app/shell/gateway-menu-panel'
import { SubagentsPopover } from '@/app/shell/subagents-popover'
import { useI18n } from '@/i18n'
import { Activity, AlertCircle, Hash, Loader2, Users, Zap, ZapFilled } from '@/lib/icons'
import { formatModelStatusLabel } from '@/lib/model-status-label'
import type { RuntimeReadinessResult } from '@/lib/runtime-readiness'
import { contextBarLabel, LiveDuration, usageContextLabel } from '@/lib/statusbar'
import { cn } from '@/lib/utils'
import { setGlobalYolo, setSessionYolo } from '@/lib/yolo-session'
import { $desktopActionTasks } from '@/store/activity'
import {
  $activeSessionId,
  $busy,
  $connection,
  $currentFastMode,
  $currentModel,
  $currentProvider,
  $currentReasoningEffort,
  $currentUsage,
  $sessionStartedAt,
  $turnStartedAt,
  $workingSessionIds,
  $yoloActive,
  setYoloActive
} from '@/store/session'
import { $subagentsBySession, type SubagentProgress } from '@/store/subagents'
import { $updateStatus, openUpdateDialog, selectTargetVersion } from '@/store/update'
import { $desktopVersion } from '@/store/version'
import type { StatusResponse } from '@/types/zast'

import type { StatusbarItem, StatusbarSelectModifiers } from '../statusbar-controls'

const EMPTY_SUBAGENTS: readonly SubagentProgress[] = []

interface StatusbarItemsOptions {
  extraLeftItems: readonly StatusbarItem[]
  extraRightItems: readonly StatusbarItem[]
  gatewayState: string
  inferenceStatus: RuntimeReadinessResult | null
  freshDraftReady: boolean
  requestGateway: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>
  resumeSession: (storedSessionId: string, replaceRoute?: boolean) => Promise<void>
  statusSnapshot: StatusResponse | null
}

export function useStatusbarItems({
  extraLeftItems,
  extraRightItems,
  gatewayState,
  inferenceStatus,
  freshDraftReady,
  requestGateway,
  resumeSession,
  statusSnapshot
}: StatusbarItemsOptions) {
  const { t } = useI18n()
  const copy = t.shell.statusbar
  const activeSessionId = useStore($activeSessionId)
  const yoloActive = useStore($yoloActive)
  const busy = useStore($busy)
  const currentFastMode = useStore($currentFastMode)
  const currentModel = useStore($currentModel)
  const currentProvider = useStore($currentProvider)
  const currentReasoningEffort = useStore($currentReasoningEffort)
  const currentUsage = useStore($currentUsage)
  const desktopActionTasks = useStore($desktopActionTasks)
  const sessionStartedAt = useStore($sessionStartedAt)
  const turnStartedAt = useStore($turnStartedAt)
  const workingSessionIds = useStore($workingSessionIds)
  const subagentsBySession = useStore($subagentsBySession)
  const desktopVersion = useStore($desktopVersion)
  const connection = useStore($connection)
  const updateStatus = useStore($updateStatus)

  const contextUsage = useMemo(() => usageContextLabel(currentUsage), [currentUsage])
  const contextBar = useMemo(() => contextBarLabel(currentUsage), [currentUsage])

  // Per-session approval bypass (same scope as the TUI's Shift+Tab). On a
  // new-chat draft (no runtime session yet) we arm locally; the session-create
  // path applies it once the backend session exists.
  //
  // Shift+click flips the GLOBAL approvals.mode instead — a persistent,
  // all-sessions/CLI/TUI/cron bypass that survives restarts.
  const toggleYolo = useCallback(
    async (modifiers?: StatusbarSelectModifiers) => {
      const next = !$yoloActive.get()

      setYoloActive(next)

      if (modifiers?.shiftKey) {
        try {
          await setGlobalYolo(requestGateway, next)
        } catch {
          setYoloActive(!next)
        }

        return
      }

      const sid = $activeSessionId.get()

      if (!sid) {
        return
      }

      try {
        await setSessionYolo(requestGateway, sid, next)
      } catch {
        setYoloActive(!next)
      }
    },
    [requestGateway]
  )

  const showYoloToggle = gatewayState === 'open' && (!!activeSessionId || freshDraftReady)

  const gatewayMenuContent = useMemo(
    () => (
      <GatewayMenuPanel gatewayState={gatewayState} inferenceStatus={inferenceStatus} statusSnapshot={statusSnapshot} />
    ),
    [gatewayState, inferenceStatus, statusSnapshot]
  )

  // Subagents for the active session — drives the status-bar item label and the popover.
  const activeSubagents: readonly SubagentProgress[] = activeSessionId
    ? (subagentsBySession[activeSessionId] ?? EMPTY_SUBAGENTS)
    : EMPTY_SUBAGENTS

  const activeSubagentCount = activeSubagents.length

  const gatewayOpen = gatewayState === 'open'
  const gatewayConnecting = gatewayState === 'connecting'
  const inferenceReady = gatewayOpen && inferenceStatus?.ready === true
  const gatewayDegraded = gatewayOpen || gatewayConnecting

  const gatewayDetail = gatewayOpen
    ? inferenceStatus?.ready
      ? copy.gatewayReady
      : inferenceStatus
        ? copy.gatewayNeedsSetup
        : copy.gatewayChecking
    : gatewayConnecting
      ? copy.gatewayConnecting
      : copy.gatewayOffline

  const gatewayClassName = inferenceReady
    ? undefined
    : gatewayDegraded
      ? 'text-amber-600 hover:text-amber-600'
      : 'text-destructive hover:text-destructive'

  // Version status item. Reflects inner-desktop auto-update state from
  // @/store/update. When a release is available, downloading, or downloaded
  // we render an amber "v{current} ↑" badge that opens the update dialog
  // on click; otherwise the item is the plain text version. Only the
  // Electron binary is self-updated by this path — the Python agent and
  // outer Tauri installer are out of scope.
  //
  // Subscribe by primitive (status / version), not the whole `updateStatus`
  // object — the IPC layer emits a fresh object on every progress tick, which
  // would force `coreRightStatusbarItems` to recompute its entire array ~30
  // times per download even when only the percent changes.
  const updatePhase = updateStatus.status

  const updateTargetVersion =
    updatePhase === 'available' || updatePhase === 'downloading' || updatePhase === 'downloaded'
      ? selectTargetVersion(updateStatus)
      : null

  const clientVersionItem = useMemo<StatusbarItem | null>(() => {
    const appVersion = desktopVersion?.appVersion

    if (!appVersion) {
      return null
    }

    const updateAvailable = updateTargetVersion !== null

    if (!updateAvailable) {
      return {
        icon: <Hash className="size-3" />,
        id: 'version-client',
        label: `v${appVersion}`,
        variant: 'text'
      }
    }

    const isDownloaded = updatePhase === 'downloaded'
    const isDownloading = updatePhase === 'downloading'

    const tooltip = isDownloaded
      ? `v${appVersion} → v${updateTargetVersion} ready — click to restart`
      : isDownloading
        ? `Downloading v${updateTargetVersion}…`
        : `Update available: v${updateTargetVersion} — click for details`

    return {
      icon: <Hash className="size-3" />,
      id: 'version-client',
      label: `v${appVersion} ↑`,
      className: 'text-amber-600',
      title: tooltip,
      onSelect: () => openUpdateDialog(),
      variant: 'action'
    }
  }, [desktopVersion?.appVersion, updatePhase, updateTargetVersion])

  const coreLeftStatusbarItems = useMemo<readonly StatusbarItem[]>(
    () => [
      {
        className: gatewayClassName,
        detail: gatewayDetail,
        icon: inferenceReady ? <Activity className="size-3" /> : <AlertCircle className="size-3" />,
        id: 'gateway-health',
        label: copy.gateway,
        menuClassName: 'w-72',
        menuContent: gatewayMenuContent,
        title: inferenceStatus?.reason || copy.gatewayTitle,
        variant: 'menu'
      }
    ],
    [copy, gatewayMenuContent, gatewayClassName, gatewayDetail, inferenceReady, inferenceStatus?.reason]
  )

  const coreRightStatusbarItems = useMemo<readonly StatusbarItem[]>(
    () => [
      {
        detail: <LiveDuration since={turnStartedAt} />,
        hidden: !busy || !turnStartedAt,
        icon: <Loader2 className="size-3 animate-spin" />,
        id: 'running-timer',
        label: copy.turnRunning,
        title: copy.currentTurnElapsed,
        variant: 'text'
      },
      {
        detail: contextBar || undefined,
        hidden: !contextUsage,
        id: 'context-usage',
        label: contextUsage,
        title: copy.contextUsage,
        variant: 'text'
      },
      {
        hidden: !activeSessionId,
        icon: <Users className="size-3" />,
        id: 'subagents',
        label: copy.subagents(activeSubagentCount),
        menuAlign: 'end',
        menuClassName: 'w-80',
        menuContent: <SubagentsPopover items={activeSubagents} onOpen={resumeSession} />,
        title: copy.openAgents,
        variant: 'menu'
      },
      {
        detail: <LiveDuration since={sessionStartedAt} />,
        hidden: !sessionStartedAt,
        id: 'session-timer',
        label: copy.session,
        title: copy.runtimeSessionElapsed,
        variant: 'text'
      },
      {
        className: cn('px-1', yoloActive && 'bg-(--chrome-action-hover)'),
        hidden: !showYoloToggle,
        icon: yoloActive ? (
          <ZapFilled className="size-3.5 shrink-0" />
        ) : (
          <Zap className="size-3.5 shrink-0 opacity-70" />
        ),
        id: 'yolo',
        onSelect: modifiers => void toggleYolo(modifiers),
        title: yoloActive ? copy.yoloOn : copy.yoloOff,
        variant: 'action'
      },
      // model-summary: hidden until an active session reports its model.
      // LLM config is owned by Backend — Desktop has no concept of an
      // "unconfigured" model, so an empty `$currentModel` just means
      // there's nothing session-scoped to show yet.
      {
        hidden: !currentModel,
        id: 'model-summary',
        label: (
          <span className="inline-flex min-w-0 items-center gap-0.5">
            <span className="truncate">
              {formatModelStatusLabel(currentModel, {
                fastMode: currentFastMode,
                reasoningEffort: currentReasoningEffort
              })}
            </span>
          </span>
        ),
        title: copy.providerModelTitle(currentProvider, currentModel),
        variant: 'text' as const
      },
      ...(clientVersionItem ? [clientVersionItem] : [])
    ],
    [
      busy,
      contextBar,
      contextUsage,
      copy,
      currentFastMode,
      currentModel,
      currentProvider,
      currentReasoningEffort,
      activeSessionId,
      activeSubagentCount,
      activeSubagents,
      resumeSession,
      sessionStartedAt,
      showYoloToggle,
      toggleYolo,
      turnStartedAt,
      clientVersionItem,
      yoloActive
    ]
  )

  const leftStatusbarItems = useMemo(
    () => [...coreLeftStatusbarItems, ...extraLeftItems],
    [coreLeftStatusbarItems, extraLeftItems]
  )

  const statusbarItems = useMemo(
    () => [...extraRightItems, ...coreRightStatusbarItems],
    [coreRightStatusbarItems, extraRightItems]
  )

  return { leftStatusbarItems, statusbarItems }
}
