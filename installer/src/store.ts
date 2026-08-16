import { atom, computed } from 'nanostores'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'
import { invoke } from '@tauri-apps/api/core'

/*
 * Bootstrap state store — single source of truth for installer screens.
 *
 * Lives in nanostores per the project's TypeScript guidelines (desktop
 * AGENTS.md): "Prefer small nanostores over component state when state is
 * shared, reused, or read by distant UI."
 *
 * One channel from Rust ('bootstrap' event), discriminated by payload.type.
 * We translate those events into typed atom updates here so the rest of
 * the app only deals with React-friendly state.
 */

export interface StageInfo {
  name: string
  title: string
  category: string
  needs_user_input: boolean
}

export type StageState = 'running' | 'succeeded' | 'skipped' | 'failed'

export interface StageRecord {
  info: StageInfo
  state: StageState | null
  durationMs?: number
  error?: string
}

export interface BootstrapStateModel {
  status: 'idle' | 'running' | 'completed' | 'failed'
  protocolVersion: number | null
  stages: Record<string, StageRecord>
  stageOrder: string[]
  currentStage: string | null
  installRoot: string | null
  error: string | null
  logs: Array<{ stage?: string; line: string; stream?: 'stdout' | 'stderr' }>
}

const INITIAL: BootstrapStateModel = {
  status: 'idle',
  protocolVersion: null,
  stages: {},
  stageOrder: [],
  currentStage: null,
  installRoot: null,
  error: null,
  logs: []
}

export type Route = 'welcome' | 'progress' | 'success' | 'failure'

export const $route = atom<Route>('welcome')
export const $bootstrap = atom<BootstrapStateModel>(INITIAL)
export const $logPath = atom<string | null>(null)
export const $spiritAgentHome = atom<string | null>(null)

export const $progress = computed($bootstrap, (b) => {
  const total = b.stageOrder.length
  if (total === 0) return { done: 0, total: 0, fraction: 0 }
  let done = 0
  for (const name of b.stageOrder) {
    const s = b.stages[name]?.state
    if (s === 'succeeded' || s === 'skipped' || s === 'failed') done += 1
  }
  return { done, total, fraction: done / total }
})

interface BootstrapManifestEvent {
  type: 'manifest'
  stages: StageInfo[]
  protocolVersion: number | null
}

interface BootstrapStageEvent {
  type: 'stage'
  name: string
  state: StageState
  durationMs?: number
  result?: {
    stage: string
    ok: boolean
    skipped: boolean
    reason?: string
  }
  error?: string
}

interface BootstrapLogEvent {
  type: 'log'
  stage?: string
  line: string
  stream?: 'stdout' | 'stderr'
}

interface BootstrapCompleteEvent {
  type: 'complete'
  installRoot: string
  marker: unknown
}

interface BootstrapFailedEvent {
  type: 'failed'
  stage?: string
  error: string
}

type BootstrapEvent =
  | BootstrapManifestEvent
  | BootstrapStageEvent
  | BootstrapLogEvent
  | BootstrapCompleteEvent
  | BootstrapFailedEvent

let unlisten: UnlistenFn | null = null
let routeTimer: ReturnType<typeof setTimeout> | null = null

function clearRouteTimer(): void {
  if (routeTimer != null) {
    clearTimeout(routeTimer)
    routeTimer = null
  }
}

export async function initialize(): Promise<void> {
  if (unlisten) return

  // Clean up IPC listeners and route timers when the installer window unloads.
  const cleanup = () => {
    clearRouteTimer()
    if (unlisten) {
      unlisten()
      unlisten = null
    }
  }

  if (typeof window !== 'undefined') {
    window.addEventListener('beforeunload', cleanup)
  }

  // Pull static info on mount for the diagnostics footer.
  try {
    const [logPath, spiritAgentHome] = await Promise.all([
      invoke<string>('get_log_path'),
      invoke<string>('get_spiritagent_home')
    ])
    $logPath.set(logPath)
    $spiritAgentHome.set(spiritAgentHome)
  } catch (err) {
    console.warn('failed to fetch installer paths', err)
  }

  unlisten = await listen<BootstrapEvent>('bootstrap', (event) => {
    const payload = event.payload
    const cur = $bootstrap.get()
    switch (payload.type) {
      case 'manifest': {
        clearRouteTimer()
        const stages: Record<string, StageRecord> = {}
        const order: string[] = []
        for (const s of payload.stages) {
          stages[s.name] = { info: s, state: null }
          order.push(s.name)
        }
        $bootstrap.set({
          ...cur,
          status: 'running',
          protocolVersion: payload.protocolVersion,
          stages,
          stageOrder: order,
          currentStage: null,
          installRoot: null,
          error: null,
          logs: []
        })
        $route.set('progress')
        break
      }
      case 'stage': {
        const existing = cur.stages[payload.name]
        if (!existing) {
          console.warn('stage event for unknown stage', payload.name)
          break
        }
        const next: StageRecord = {
          ...existing,
          state: payload.state,
          durationMs: payload.durationMs,
          error: payload.error
        }
        $bootstrap.set({
          ...cur,
          stages: { ...cur.stages, [payload.name]: next },
          currentStage:
            payload.state === 'running' ? payload.name : cur.currentStage
        })
        break
      }
      case 'log': {
        const logs = [...cur.logs, { stage: payload.stage, line: payload.line, stream: payload.stream }]
        // Keep the rolling buffer bounded so the UI doesn't get OOM'd
        // during a long install (playwright chromium download is ~10k lines).
        const trimmed = logs.length > 2000 ? logs.slice(-2000) : logs
        $bootstrap.set({ ...cur, logs: trimmed })
        break
      }
      case 'complete':
        clearRouteTimer()
        $bootstrap.set({
          ...cur,
          status: 'completed',
          installRoot: payload.installRoot,
          currentStage: null
        })
        routeTimer = setTimeout(() => {
          routeTimer = null
          $route.set('success')
        }, 2200)
        break
      case 'failed':
        clearRouteTimer()
        $bootstrap.set({
          ...cur,
          status: 'failed',
          error: payload.error,
          currentStage: null
        })
        routeTimer = setTimeout(() => {
          routeTimer = null
          $route.set('failure')
        }, 1500)
        break
    }
  })
}

export async function startInstall(): Promise<void> {
  clearRouteTimer()
  // Reset before kicking off so a retry from the failure screen clears
  // the previous run's state. The install script is bundled and pinned at
  // build time (BUILD_PIN_BRANCH) — branch/commit are always null here.
  $bootstrap.set(INITIAL)
  $route.set('progress')
  await invoke('start_bootstrap', {
    args: {
      commit: null,
      branch: null,
      include_desktop: true,
      spiritagent_home: null
    }
  })
}

export async function cancelInstall(): Promise<void> {
  clearRouteTimer()
  await invoke('cancel_bootstrap')
}

export async function launchSpiritAgentDesktop(): Promise<void> {
  if (!$bootstrap.get().installRoot) throw new Error('no install root')
  // launch_spiritagent_desktop resolves the desktop binary from $SPIRITAGENT_HOME
  // on the Rust side (see src-tauri/src/bootstrap.rs).
  await invoke('launch_spiritagent_desktop')
}

export async function openLogDir(): Promise<void> {
  await invoke('open_log_dir')
}
