export interface DesktopVersionInfo {
  appVersion: string
  electronVersion: string
  nodeVersion: string
  platform: string
}

export interface DesktopUpdateInfo {
  releaseDate?: string
  releaseNotes?: string
  version: string
}

export interface DesktopUpdateProgress {
  bytesPerSecond: number
  delta: number
  percent: number
  total: number
  transferred: number
}

export type DesktopUpdateEvent =
  | { info?: DesktopUpdateInfo; type: 'available' }
  | { info?: DesktopUpdateInfo; type: 'downloaded' }
  | { info?: DesktopUpdateInfo; type: 'none' }
  | { message: string; type: 'error' }
  | { progress: DesktopUpdateProgress; type: 'progress' }
  | { type: 'checking' }

export type DesktopRunnerUpdateEvent =
  | { error: string; kind: 'runner-failed'; recoverable: boolean; version?: string }
  | { kind: 'runner-installed'; version: string }
  | { kind: 'runner-installing'; percent?: number; phase: 'pip' | 'starting'; version: string }
  | { kind: 'runner-prefetching'; percent?: number; phase: 'manifest' | 'server' | 'wheel'; version: string }
  | { kind: 'runner-ready'; version: string }

export interface RunnerCapabilities {
  local_stt?: boolean
  local_tts?: boolean
  microphone?: boolean
  platform?: string
  python?: string
  screen_capture?: boolean
  system_activity?: boolean
}

export type DesktopRunnerStatusEvent =
  | { error: Error; phase: string; type: 'error' }
  | {
      capabilities?: null | RunnerCapabilities
      probeFailed?: boolean | null
      runnerVersion?: null | string
      type: 'runner_ready'
    }
  | {
      capabilities?: null | RunnerCapabilities
      probeFailed?: boolean | null
      runnerVersion?: null | string
      tools: unknown[]
      type: 'running'
    }
  | {
      capabilities?: null | RunnerCapabilities
      tools: unknown[]
      type: 'tools_changed'
    }
  | { errors: string[]; reason?: string; type: 'stopped' }

export type DesktopRunnerPhase = 'error' | 'idle' | 'running' | 'starting' | 'stopped' | 'stopping'

export interface DesktopRunnerState {
  capabilities?: null | RunnerCapabilities
  lastError?: null | string
  phase: DesktopRunnerPhase
  probeFailed?: boolean | null
  runnerVersion?: null | string
  startedAt?: null | number
  stoppedAt?: null | number
}

export interface DeskAgentConnection {
  authMode?: string
  baseUrl: string
  isFullscreen: boolean
  logs: string[]
  mode?: 'local' | 'remote'
  nativeOverlayWidth: number
  source?: 'env' | 'local' | 'settings'
  token: null | string
  windowButtonPosition: null | { x: number; y: number }
  wsUrl: string
}

export interface DeskAgentTitleBarTheme {
  background: string
  foreground: string
}

export interface DeskAgentWindowState {
  isFullscreen: boolean
  nativeOverlayWidth: number
  windowButtonPosition: null | { x: number; y: number }
}

export interface DesktopBootProgress {
  error: null | string
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
  baseUrl: null | string
  hasToken: boolean
  tokenExpiresAt: null | number
  user: null | DesktopAuthUser
}

export interface DesktopActivatePayload {
  clientContext?: unknown
  code: string
}

export interface DesktopLogoutResult {
  backendUnreachable?: boolean
  error?: string
  ok: boolean
}

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

export interface SkillItem {
  category: string
  compatible: boolean
  description?: string
  enabled: boolean
  name: string
  platforms?: null | string[]
}

export interface ToolsetItem {
  enabled: boolean
  id: string
  toolNames: string[]
}

export interface RunnerConfigPatch {
  op?: 'delete' | 'set'
  path: readonly (number | string)[]
  value?: unknown
}

export interface MediaSttPayload {
  context?: null | string
  dataUrl: string
  filename?: string
  language?: string
}

export interface MediaTtsPayload {
  context?: null | string
  persist?: boolean
  text: string
  voice?: string
}

// 1. Request-Response (Renderer -> Main via ipcRenderer.invoke / ipcMain.handle)
export interface IpcInvokeContract {
  // Connection & Boot
  'deskagent:connection': () => DeskAgentConnection | Promise<DeskAgentConnection>
  'deskagent:gateway:ws-url': () => Promise<string> | string
  'deskagent:boot-progress:get': () => DesktopBootProgress | Promise<DesktopBootProgress>

  // Auth
  'deskagent:auth:activate': (payload: DesktopActivatePayload) => DesktopAuthSnapshot | Promise<DesktopAuthSnapshot>
  'deskagent:auth:refresh': (payload?: Record<string, unknown>) => DesktopAuthSnapshot | Promise<DesktopAuthSnapshot>
  'deskagent:auth:logout': () => DesktopLogoutResult | Promise<DesktopLogoutResult>
  'deskagent:auth:get-session': () => DesktopAuthSnapshot | null | Promise<DesktopAuthSnapshot | null>
  'deskagent:auth:get-default-backend-url': () => null | string | Promise<null | string>

  // Windows & UI
  'deskagent:window:show-tool': () => Promise<void> | void

  // Backend API Proxy
  'deskagent:api': (request: DeskAgentApiRequest) => Promise<unknown> | unknown
  'deskagent:api:asset': (request: { url: string }) => Promise<string> | string
  'deskagent:api:asset-cached-path': (request: {
    contentHash?: string
    url: string
  }) => null | string | Promise<null | string>
  'deskagent:api:asset-buffer': (request: { contentHash?: string; url: string }) => Promise<Uint8Array> | Uint8Array

  // File & Clipboard & Log
  'deskagent:readFileDataUrl': (filePath: string) => Promise<string> | string
  'deskagent:selectPaths': (options?: DeskAgentSelectPathsOptions) => Promise<string[]> | string[]
  'deskagent:writeClipboard': (text: string) => boolean | Promise<boolean>
  'deskagent:saveClipboardImage': () => Promise<string> | string
  'deskagent:log:emit': (payload: {
    args: unknown[]
    level: 'error' | 'info' | 'warn'
    scope: string
  }) => Promise<void> | void
  'deskagent:version': () => DesktopVersionInfo | Promise<DesktopVersionInfo>

  // Runner
  'deskagent:runner:invoke': (name: string, args: Record<string, unknown>) => Promise<unknown> | unknown
  'deskagent:runner:reload-mcp': () => Promise<unknown> | unknown
  'deskagent:runner:get-state': () => DesktopRunnerState | Promise<DesktopRunnerState>
  'deskagent:runner:get-tools': () => Array<Record<string, unknown>> | Promise<Array<Record<string, unknown>>>
  'deskagent:runner-config:read': () =>
    | { content?: string; error?: string; ok: boolean }
    | Promise<{ content?: string; error?: string; ok: boolean }>
  'deskagent:runner-config:write': (
    configString: string
  ) => { error?: string; ok: boolean } | Promise<{ error?: string; ok: boolean }>
  'deskagent:runner-config:patch': (
    patch: RunnerConfigPatch
  ) => { error?: string; ok: boolean } | Promise<{ error?: string; ok: boolean }>

  // Skills & Toolsets
  'deskagent:skills:list': () =>
    | { error?: string; ok: boolean; skills?: SkillItem[] }
    | Promise<{ error?: string; ok: boolean; skills?: SkillItem[] }>
  'deskagent:skill:set-enabled': (payload: {
    enabled: boolean
    name: string
  }) =>
    | { error?: string; ok: boolean; skills?: SkillItem[] }
    | Promise<{ error?: string; ok: boolean; skills?: SkillItem[] }>
  'deskagent:toolsets:list': () =>
    | { error?: string; ok: boolean; toolsets?: ToolsetItem[] }
    | Promise<{ error?: string; ok: boolean; toolsets?: ToolsetItem[] }>
  'deskagent:toolset:set-enabled': (payload: {
    enabled: boolean
    id: string
  }) =>
    | { error?: string; ok: boolean; toolsets?: ToolsetItem[] }
    | Promise<{ error?: string; ok: boolean; toolsets?: ToolsetItem[] }>

  // Media
  'deskagent:media:stt': (payload: MediaSttPayload) => { text: string } | Promise<{ text: string }>
  'deskagent:media:tts': (
    payload: MediaTtsPayload
  ) => { dataUrl: string; mimeType: string } | Promise<{ dataUrl: string; mimeType: string }>
  'deskagent:onboardingAudio:read': (
    tag: string
  ) =>
    | { bytes: number; dataUrl: string; mimeType: string; tag: string }
    | Promise<{ bytes: number; dataUrl: string; mimeType: string; tag: string }>

  // Sprite
  'deskagent:sprite:set-ignore-mouse-events': (payload: { forward?: boolean; ignore: boolean }) => Promise<void> | void
  'deskagent:sprite:set-always-on-top': (payload: { on: boolean }) => Promise<void> | void
  'deskagent:sprite:get-position': () => null | { x: number; y: number } | Promise<null | { x: number; y: number }>
  'deskagent:sprite:set-position': (payload: { x: number; y: number }) => Promise<void> | void

  // Update
  'deskagent:update:check': () => Promise<void> | void
}

// 2. Events pushed Main -> Renderer (via webContents.send / ipcRenderer.on)
export interface IpcEventContract {
  'deskagent:auth:changed': [payload: DesktopAuthBroadcast]
  'deskagent:auth:session-expired': []
  'deskagent:boot-progress': [payload: DesktopBootProgress]
  'deskagent:power-resume': []
  'deskagent:runner-update-event': [payload: DesktopRunnerUpdateEvent]
  'deskagent:runner:status': [payload: DesktopRunnerStatusEvent]
  'deskagent:tray:logout': []
  'deskagent:update-event': [payload: DesktopUpdateEvent]
  'deskagent:window-state-changed': [payload: DeskAgentWindowState]
}

// 3. Unidirectional messages Renderer -> Main (via ipcRenderer.send / ipcMain.on)
export interface IpcSendContract {
  'deskagent:titlebar-theme': [payload: DeskAgentTitleBarTheme]
}

export type IpcChannel = keyof IpcInvokeContract
export type IpcEventChannel = keyof IpcEventContract
export type IpcSendChannel = keyof IpcSendContract
