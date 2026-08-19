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

export interface CapabilityHealthItem {
  available: boolean
  reason?: string | null
}

export type RunnerCapabilitiesHealth = Record<string, CapabilityHealthItem>

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
      capabilitiesHealth?: null | RunnerCapabilitiesHealth
      probeFailed?: boolean | null
      runnerVersion?: null | string
      type: 'runner_ready'
    }
  | {
      capabilities?: null | RunnerCapabilities
      capabilitiesHealth?: null | RunnerCapabilitiesHealth
      probeFailed?: boolean | null
      runnerVersion?: null | string
      tools: unknown[]
      type: 'running'
    }
  | {
      capabilities?: null | RunnerCapabilities
      capabilitiesHealth?: null | RunnerCapabilitiesHealth
      tools: unknown[]
      type: 'tools_changed'
    }
  | { errors: string[]; reason?: string; type: 'stopped' }

export type DesktopRunnerPhase = 'error' | 'idle' | 'running' | 'starting' | 'stopped' | 'stopping'

export interface DesktopRunnerState {
  capabilities?: null | RunnerCapabilities
  capabilitiesHealth?: null | RunnerCapabilitiesHealth
  lastError?: null | string
  phase: DesktopRunnerPhase
  probeFailed?: boolean | null
  runnerVersion?: null | string
  startedAt?: null | number
  stoppedAt?: null | number
}

export interface SpiritAgentConnection {
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

export interface SpiritAgentTitleBarTheme {
  background: string
  foreground: string
}

export interface SpiritAgentWindowState {
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

export interface SpiritAgentApiRequest {
  body?: unknown
  method?: string
  path: string
  timeoutMs?: number
}

export interface SpiritAgentSelectPathsOptions {
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
  // 连接与启动
  'spiritagent:connection': () => SpiritAgentConnection | Promise<SpiritAgentConnection>
  'spiritagent:gateway:ws-url': () => Promise<string> | string
  'spiritagent:boot-progress:get': () => DesktopBootProgress | Promise<DesktopBootProgress>

  // 鉴权
  'spiritagent:auth:activate': (payload: DesktopActivatePayload) => DesktopAuthSnapshot | Promise<DesktopAuthSnapshot>
  'spiritagent:auth:refresh': (payload?: Record<string, unknown>) => DesktopAuthSnapshot | Promise<DesktopAuthSnapshot>
  'spiritagent:auth:logout': () => DesktopLogoutResult | Promise<DesktopLogoutResult>
  'spiritagent:auth:get-session': () => DesktopAuthSnapshot | null | Promise<DesktopAuthSnapshot | null>
  'spiritagent:auth:get-default-backend-url': () => null | string | Promise<null | string>

  // 窗口与界面
  'spiritagent:window:show-tool': () => Promise<void> | void

  // 后端 API 代理
  'spiritagent:api': (request: SpiritAgentApiRequest) => Promise<unknown> | unknown
  'spiritagent:api:asset': (request: { url: string }) => Promise<string> | string
  'spiritagent:api:asset-buffer': (request: { contentHash?: string; url: string }) => Promise<Uint8Array> | Uint8Array

  // 文件 / 剪贴板 / 日志
  'spiritagent:readFileDataUrl': (filePath: string) => Promise<string> | string
  'spiritagent:selectPaths': (options?: SpiritAgentSelectPathsOptions) => Promise<string[]> | string[]
  'spiritagent:writeClipboard': (text: string) => boolean | Promise<boolean>
  'spiritagent:saveClipboardImage': () => Promise<string> | string
  'spiritagent:log:emit': (payload: {
    args: unknown[]
    level: 'error' | 'info' | 'warn'
    scope: string
  }) => Promise<void> | void
  'spiritagent:version': () => DesktopVersionInfo | Promise<DesktopVersionInfo>

  // Runner
  'spiritagent:runner:invoke': (name: string, args: Record<string, unknown>) => Promise<unknown> | unknown
  'spiritagent:runner:reload-mcp': () => Promise<unknown> | unknown
  'spiritagent:runner:get-state': () => DesktopRunnerState | Promise<DesktopRunnerState>
  'spiritagent:runner:get-tools': () => Array<Record<string, unknown>> | Promise<Array<Record<string, unknown>>>
  'spiritagent:runner-config:read': () =>
    | { content?: string; error?: string; ok: boolean }
    | Promise<{ content?: string; error?: string; ok: boolean }>
  'spiritagent:runner-config:write': (
    configString: string
  ) => { error?: string; ok: boolean } | Promise<{ error?: string; ok: boolean }>
  'spiritagent:runner-config:patch': (
    patch: RunnerConfigPatch
  ) => { error?: string; ok: boolean } | Promise<{ error?: string; ok: boolean }>

  // Skills 与工具集
  'spiritagent:skills:list': () =>
    | { error?: string; ok: boolean; skills?: SkillItem[] }
    | Promise<{ error?: string; ok: boolean; skills?: SkillItem[] }>
  'spiritagent:skill:set-enabled': (payload: {
    enabled: boolean
    name: string
  }) =>
    | { error?: string; ok: boolean; skills?: SkillItem[] }
    | Promise<{ error?: string; ok: boolean; skills?: SkillItem[] }>
  'spiritagent:toolsets:list': () =>
    | { error?: string; ok: boolean; toolsets?: ToolsetItem[] }
    | Promise<{ error?: string; ok: boolean; toolsets?: ToolsetItem[] }>
  'spiritagent:toolset:set-enabled': (payload: {
    enabled: boolean
    id: string
  }) =>
    | { error?: string; ok: boolean; toolsets?: ToolsetItem[] }
    | Promise<{ error?: string; ok: boolean; toolsets?: ToolsetItem[] }>

  // 媒体
  'spiritagent:media:stt': (payload: MediaSttPayload) => { text: string } | Promise<{ text: string }>
  'spiritagent:media:tts': (
    payload: MediaTtsPayload
  ) => { dataUrl: string; mimeType: string } | Promise<{ dataUrl: string; mimeType: string }>
  'spiritagent:onboardingAudio:read': (
    tag: string
  ) =>
    | { bytes: number; dataUrl: string; mimeType: string; tag: string }
    | Promise<{ bytes: number; dataUrl: string; mimeType: string; tag: string }>

  // 精灵窗口
  'spiritagent:sprite:hide': () => Promise<void> | void
  'spiritagent:sprite:set-ignore-mouse-events': (payload: {
    forward?: boolean
    ignore: boolean
  }) => Promise<void> | void
  'spiritagent:sprite:set-always-on-top': (payload: { on: boolean }) => Promise<void> | void
  'spiritagent:sprite:get-position': () =>
    | null
    | { origin?: { x: number; y: number }; x: number; y: number }
    | Promise<null | { origin?: { x: number; y: number }; x: number; y: number }>
  'spiritagent:sprite:set-position': (payload: { x: number; y: number }) => Promise<void> | void
  'spiritagent:sprite:move-to-cursor-display': () =>
    | null
    | { cursor: { x: number; y: number }; from: { x: number; y: number }; to: { x: number; y: number } }
    | Promise<null | { cursor: { x: number; y: number }; from: { x: number; y: number }; to: { x: number; y: number } }>

  // 更新
  'spiritagent:update:check': () => Promise<void> | void
}

// 2. Events pushed Main -> Renderer (via webContents.send / ipcRenderer.on)
export interface IpcEventContract {
  'spiritagent:auth:changed': [payload: DesktopAuthBroadcast]
  'spiritagent:auth:session-expired': []
  'spiritagent:boot-progress': [payload: DesktopBootProgress]
  'spiritagent:power-resume': []
  'spiritagent:runner-update-event': [payload: DesktopRunnerUpdateEvent]
  'spiritagent:runner:status': [payload: DesktopRunnerStatusEvent]
  'spiritagent:tray:logout': []
  'spiritagent:update-event': [payload: DesktopUpdateEvent]
  'spiritagent:window-state-changed': [payload: SpiritAgentWindowState]
}

// 3. Unidirectional messages Renderer -> Main (via ipcRenderer.send / ipcMain.on)
export interface IpcSendContract {
  'spiritagent:titlebar-theme': [payload: SpiritAgentTitleBarTheme]
}

export type IpcChannel = keyof IpcInvokeContract
export type IpcEventChannel = keyof IpcEventContract
export type IpcSendChannel = keyof IpcSendContract
