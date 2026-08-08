export {}

declare global {
  interface Window {
    deskagent: {
      getConnection: () => Promise<DeskAgentConnection>
      getGatewayWsUrl: () => Promise<string>
      getBootProgress: () => Promise<DesktopBootProgress>
      login: (payload: DesktopLoginPayload) => Promise<DesktopAuthSnapshot>
      refreshSession: (payload?: Record<string, unknown>) => Promise<DesktopAuthSnapshot>
      logout: () => Promise<DesktopLogoutResult>
      getSession: () => Promise<DesktopAuthSnapshot | null>
      getDefaultBackendUrl: () => Promise<string | null>
      showToolWindow: () => Promise<void>
      api: <T>(request: DeskAgentApiRequest) => Promise<T>
      /** Fetch a backend-served binary asset as a data URL (see connection.cjs). */
      apiAsset: (request: { url: string }) => Promise<string>
      /** Fetch a backend-served binary asset as raw bytes — for large payloads (GLB) where base64 inflation is unacceptable. */
      apiAssetBuffer: (request: { url: string }) => Promise<Uint8Array>
      readFileDataUrl: (filePath: string) => Promise<string>
      selectPaths: (options?: DeskAgentSelectPathsOptions) => Promise<string[]>
      writeClipboard: (text: string) => Promise<boolean>
      saveClipboardImage: () => Promise<string>
      runnerInvoke?: (name: string, args: Record<string, unknown>) => Promise<unknown>
      runnerGetTools?: () => Promise<Array<Record<string, unknown>>>
      getPathForFile: (file: File) => string
      setTitleBarTheme?: (payload: DeskAgentTitleBarTheme) => void
      runnerConfig: {
        read: () => Promise<{ ok: boolean; content?: string; error?: string }>
        write: (
          configString: string
        ) => Promise<{ ok: boolean; restarted?: boolean; restartError?: string; error?: string }>
        patch: (patch: {
          path: readonly (string | number)[]
          value?: unknown
          op?: 'set' | 'delete'
        }) => Promise<{ ok: boolean; restarted?: boolean; restartError?: string; error?: string }>
      }
      skills: {
        list: () => Promise<{
          ok: boolean
          skills?: Array<{
            category: string
            name: string
            description?: string
            platforms?: string[] | null
            compatible: boolean
            enabled: boolean
          }>
          error?: string
        }>
        setEnabled: (payload: { name: string; enabled: boolean }) => Promise<{
          ok: boolean
          skills?: Array<{
            category: string
            name: string
            description?: string
            platforms?: string[] | null
            compatible: boolean
            enabled: boolean
          }>
          error?: string
        }>
      }
      toolsets: {
        list: () => Promise<{
          ok: boolean
          toolsets?: Array<{ id: string; toolNames: string[]; enabled: boolean }>
          error?: string
        }>
        setEnabled: (payload: { id: string; enabled: boolean }) => Promise<{
          ok: boolean
          toolsets?: Array<{ id: string; toolNames: string[]; enabled: boolean }>
          error?: string
        }>
      }
      changePassword: (payload: {
        current_password: string
        new_password: string
      }) => Promise<{ ok: boolean; message?: string }>
      media: {
        stt: (payload: {
          context?: string | null
          dataUrl: string
          filename?: string
          language?: string
        }) => Promise<{ text: string }>
        tts: (payload: {
          context?: string | null
          text: string
          voice?: string
        }) => Promise<{ dataUrl: string; mimeType: string }>
        onboardingAudio: {
          read: (tag: string) => Promise<{ dataUrl: string; mimeType: string; tag: string; bytes: number }>
        }
      }
      sprite: {
        setIgnoreMouseEvents: (payload: { ignore: boolean; forward?: boolean }) => Promise<void>
        setAlwaysOnTop: (payload: { on: boolean }) => Promise<void>
        getPosition: () => Promise<{ x: number; y: number } | null>
        setPosition: (payload: { x: number; y: number }) => Promise<void>
      }
      onWindowStateChanged?: (callback: (payload: DeskAgentWindowState) => void) => () => void
      onPowerResume?: (callback: () => void) => () => void
      onBootProgress: (callback: (payload: DesktopBootProgress) => void) => () => void
      onSessionExpired: (callback: () => void) => () => void
      onAuthChanged: (callback: (payload: DesktopAuthBroadcast) => void) => () => void
      onRunnerStatus?: (callback: (payload: DesktopRunnerStatusEvent) => void) => () => void
      onTrayLogout?: (callback: () => void) => () => void
      getVersion: () => Promise<DesktopVersionInfo>
      update?: {
        check: () => Promise<void>
        onEvent: (callback: (payload: DesktopUpdateEvent) => void) => () => void
        onRunnerEvent: (callback: (payload: DesktopRunnerUpdateEvent) => void) => () => void
      }
    }
  }
}

export interface DesktopVersionInfo {
  appVersion: string
  electronVersion: string
  nodeVersion: string
  platform: string
}

export interface DesktopUpdateInfo {
  version: string
  releaseDate?: string
  releaseNotes?: string
}

export interface DesktopUpdateProgress {
  bytesPerSecond: number
  delta: number
  percent: number
  total: number
  transferred: number
}

export type DesktopUpdateEvent =
  | { type: 'checking' }
  | { type: 'available'; info?: DesktopUpdateInfo }
  | { type: 'none'; info?: DesktopUpdateInfo }
  | { type: 'progress'; progress: DesktopUpdateProgress }
  | { type: 'downloaded'; info?: DesktopUpdateInfo }
  | { type: 'error'; message: string }

// Runner-side update events, forwarded from main.cjs → runner-updater.cjs
// on the `deskagent:runner-update-event` IPC channel. Phase 1 (prefetch) runs in
// the OLD Electron after `update-downloaded`; phase 2 (install) runs in the
// NEW Electron at startup. `recoverable: false` means the user must reinstall.
export type DesktopRunnerUpdateEvent =
  | { kind: 'runner-prefetching'; version: string; phase: 'manifest' | 'wheel' | 'server'; percent?: number }
  | { kind: 'runner-ready'; version: string }
  | { kind: 'runner-installing'; version: string; phase: 'pip' | 'starting'; percent?: number }
  | { kind: 'runner-installed'; version: string }
  | { kind: 'runner-failed'; error: string; recoverable: boolean; version?: string }

// Runner capabilities probe result. Reflected from `runner_ready` events in
// runner-bridge.cjs and the `running` / `tools_changed` lifecycle variants —
// each capability maps to a real per-platform subsystem probe
// (sounddevice, Win32 GetLastInputInfo, Quartz, loginctl, …).
export interface RunnerCapabilities {
  microphone?: boolean
  screen_capture?: boolean
  local_stt?: boolean
  local_tts?: boolean
  system_activity?: boolean
  platform?: string
  python?: string
}

// Runner lifecycle events from runner-bridge.cjs (`running` / `stopped` /
// `error` / `tools_changed`), forwarded over the `deskagent:runner:status` IPC
// channel. Renderer subscribes via `onRunnerStatus`; see use-gateway-boot.ts
// where the `running` and `tools_changed` variants both trigger a
// tool-schema sync to backend.
export type DesktopRunnerStatusEvent =
  | {
      type: 'running'
      tools: unknown[]
      capabilities?: RunnerCapabilities | null
      runnerVersion?: string | null
      probeFailed?: boolean | null
    }
  | {
      type: 'runner_ready'
      capabilities?: RunnerCapabilities | null
      runnerVersion?: string | null
      probeFailed?: boolean | null
    }
  | {
      type: 'tools_changed'
      tools: unknown[]
      capabilities?: RunnerCapabilities | null
    }
  | { type: 'stopped'; reason?: string; errors: string[] }
  | { type: 'error'; phase: string; error: Error }

export interface DeskAgentConnection {
  baseUrl: string
  isFullscreen: boolean
  mode?: 'local' | 'remote'
  nativeOverlayWidth: number
  source?: 'env' | 'local' | 'settings'
  token: string
  wsUrl: string
  logs: string[]
  windowButtonPosition: { x: number; y: number } | null
}

export interface DeskAgentTitleBarTheme {
  background: string
  foreground: string
}

export interface DeskAgentWindowState {
  isFullscreen: boolean
  nativeOverlayWidth: number
  windowButtonPosition: { x: number; y: number } | null
}

export interface DesktopBootProgress {
  error: string | null
  fakeMode: boolean
  message: string
  phase: string
  progress: number
  running: boolean
  timestamp: number
}

export interface DesktopAuthUser {
  id: number
  username: string
}

export interface DesktopAuthSnapshot {
  baseUrl: string | null
  hasToken: boolean
  tokenExpiresAt: number | null
  user: DesktopAuthUser | null
}

export interface DesktopLoginPayload {
  password: string
  username: string
  baseUrl?: string
}

export interface DesktopLogoutResult {
  backendUnreachable?: boolean
  error?: string
  ok: boolean
}

// main→renderer broadcast after every login/logout/refresh, sent to BOTH
// windows so each renderer's per-window $auth stays in sync. The sprite
// window never runs the login form, so it relies on this to learn the new
// session and boot/teardown its gateway.
export interface DesktopAuthBroadcast {
  authenticated: boolean
  snapshot: DesktopAuthSnapshot | null
}

export interface DeskAgentApiRequest {
  body?: unknown
  method?: string
  path: string
  timeoutMs?: number
}

export interface DeskAgentSelectPathsOptions {
  defaultPath?: string
  directories?: boolean
  filters?: Array<{ extensions: string[]; name: string }>
  multiple?: boolean
  title?: string
}
