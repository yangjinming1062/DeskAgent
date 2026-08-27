// SpiritAgent Electron IPC 契约 —— 主进程与渲染进程的唯一真理源。
// 通过 `@ipc/contracts` 别名同时被 `client/main/preload.ts` 和
// `client/renderer/shared/types/global.d.ts` 导入。
// 在此处新增或重命名通道/载荷字段，会在两侧类型检查时立即报错。

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

interface CapabilityHealthItem {
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
      tools?: unknown[] | null
      type: 'runner_ready'
    }
  | {
      capabilities?: null | RunnerCapabilities
      capabilitiesHealth?: null | RunnerCapabilitiesHealth
      probeFailed?: boolean | null
      runnerVersion?: null | string
      tools: unknown[] | null
      type: 'running'
    }
  | { errors?: string[]; reason?: string; type: 'stopped' }

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
  baseUrl: string
  isFullscreen: boolean
  nativeOverlayWidth: number
  token: null | string
  windowButtonPosition: null | { x: number; y: number }
  wsUrl: string
}

export interface SpiritAgentTitleBarTheme {
  background: string
  foreground: string
}

export type SpiritAgentUiTheme = 'classic' | 'cyber-glass' | 'holo'

// 主进程侧校验白名单——契约是跨进程唯一真理源，渲染层 registry 只扩展元数据。
export const SPIRITAGENT_UI_THEMES = ['classic', 'cyber-glass', 'holo'] as const satisfies readonly SpiritAgentUiTheme[]

export interface DesktopUiThemeBroadcast {
  theme: SpiritAgentUiTheme
}

export interface DesktopBootProgress {
  error: null | string
  message: string
  phase: string
  progress: number
  running: boolean
  timestamp: number
}

export interface DesktopAuthSnapshot {
  baseUrl: null | string
  hasToken: boolean
  tokenExpiresAt: null | number
  user: null | { username: string }
}

export interface DesktopActivatePayload {
  code: string
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

// 1. 请求-响应（渲染进程 -> 主进程，通过 ipcRenderer.invoke / ipcMain.handle）
export interface IpcInvokeContract {
  // 连接与启动
  'spiritagent:connection': () => SpiritAgentConnection | Promise<SpiritAgentConnection>
  'spiritagent:gateway:ws-url': () => Promise<string> | string
  'spiritagent:voice-ws-url': () => Promise<string> | string
  'spiritagent:boot-progress:get': () => DesktopBootProgress | Promise<DesktopBootProgress>

  // 鉴权
  'spiritagent:auth:activate': (payload: DesktopActivatePayload) => DesktopAuthSnapshot | Promise<DesktopAuthSnapshot>
  'spiritagent:auth:refresh': () => DesktopAuthSnapshot | Promise<DesktopAuthSnapshot>
  'spiritagent:auth:logout': () =>
    | { backendUnreachable?: boolean; error?: string; ok: boolean }
    | Promise<{ backendUnreachable?: boolean; error?: string; ok: boolean }>
  'spiritagent:auth:get-session': () => DesktopAuthSnapshot | null | Promise<DesktopAuthSnapshot | null>

  // 窗口与界面
  'spiritagent:window:show-tool': () => Promise<void> | void

  // 后端 API 代理
  'spiritagent:api': (request: SpiritAgentApiRequest) => Promise<unknown> | unknown
  'spiritagent:api:asset': (request: { url: string }) => Promise<string> | string
  'spiritagent:api:asset-buffer': (request: { contentHash?: string; url: string }) => Promise<Uint8Array> | Uint8Array
  'spiritagent:api:asset-model-url': (request: { contentHash?: string; url: string }) => string | Promise<string>

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
  'spiritagent:runner:cancel': () => unknown | Promise<unknown>
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

  'spiritagent:update:check': () => Promise<void> | void

  // 精灵窗口
  'spiritagent:sprite:hide': () => Promise<void> | void
  'spiritagent:sprite:set-ignore-mouse-events': (payload: {
    forward?: boolean
    ignore: boolean
  }) => Promise<void> | void
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
}

// 2. 主进程向渲染进程推送事件（通过 webContents.send / ipcRenderer.on）
export interface IpcEventContract {
  'spiritagent:auth:changed': [payload: DesktopAuthBroadcast]
  'spiritagent:auth:session-expired': []
  'spiritagent:boot-progress': [payload: DesktopBootProgress]
  'spiritagent:power-resume': []
  'spiritagent:runner:status': [payload: DesktopRunnerStatusEvent]
  'spiritagent:tray:activate': []
  'spiritagent:tray:logout': []
  'spiritagent:tray:open-chat': []
  'spiritagent:ui-theme-changed': [payload: DesktopUiThemeBroadcast]
  'spiritagent:update-event': [payload: DesktopUpdateEvent]
}

// 3. 渲染进程向主进程单向发送消息（通过 ipcRenderer.send / ipcMain.on）
export interface IpcSendContract {
  'spiritagent:titlebar-theme': [payload: SpiritAgentTitleBarTheme]
  'spiritagent:ui-theme': [payload: SpiritAgentUiTheme]
}

type IpcChannel = keyof IpcInvokeContract
export type IpcEventChannel = keyof IpcEventContract
export type IpcSendChannel = keyof IpcSendContract

// 运行时 channel 常量。用扁平键(camelCase)避免 `Record<string, Record<string, ...>>`
// 守卫无法适配混合扁平/嵌套 channel 名的结构问题。每个叶子字符串都必须
// 是对应契约接口的合法 key,任何拼写错误立即在 `satisfies` 检查处报错。
// 在 main + preload 中以 `IPC.invoke.authActivate` 等方式使用,完全消除字面量字符串。
export const IPC = {
  invoke: {
    authActivate: 'spiritagent:auth:activate',
    authRefresh: 'spiritagent:auth:refresh',
    authLogout: 'spiritagent:auth:logout',
    authGetSession: 'spiritagent:auth:get-session',
    connection: 'spiritagent:connection',
    gatewayWsUrl: 'spiritagent:gateway:ws-url',
    voiceWsUrl: 'spiritagent:voice-ws-url',
    bootProgressGet: 'spiritagent:boot-progress:get',
    api: 'spiritagent:api',
    apiAsset: 'spiritagent:api:asset',
    apiAssetBuffer: 'spiritagent:api:asset-buffer',
    apiAssetModelUrl: 'spiritagent:api:asset-model-url',
    windowShowTool: 'spiritagent:window:show-tool',
    readFileDataUrl: 'spiritagent:readFileDataUrl',
    selectPaths: 'spiritagent:selectPaths',
    writeClipboard: 'spiritagent:writeClipboard',
    saveClipboardImage: 'spiritagent:saveClipboardImage',
    logEmit: 'spiritagent:log:emit',
    version: 'spiritagent:version',
    runnerInvoke: 'spiritagent:runner:invoke',
    runnerCancel: 'spiritagent:runner:cancel',
    runnerGetState: 'spiritagent:runner:get-state',
    runnerGetTools: 'spiritagent:runner:get-tools',
    runnerConfigRead: 'spiritagent:runner-config:read',
    runnerConfigWrite: 'spiritagent:runner-config:write',
    runnerConfigPatch: 'spiritagent:runner-config:patch',
    skillsList: 'spiritagent:skills:list',
    skillSetEnabled: 'spiritagent:skill:set-enabled',
    toolsetsList: 'spiritagent:toolsets:list',
    toolsetSetEnabled: 'spiritagent:toolset:set-enabled',
    mediaStt: 'spiritagent:media:stt',
    mediaTts: 'spiritagent:media:tts',
    onboardingAudioRead: 'spiritagent:onboardingAudio:read',
    spriteHide: 'spiritagent:sprite:hide',
    spriteSetIgnoreMouseEvents: 'spiritagent:sprite:set-ignore-mouse-events',
    spriteGetPosition: 'spiritagent:sprite:get-position',
    spriteSetPosition: 'spiritagent:sprite:set-position',
    spriteMoveToCursorDisplay: 'spiritagent:sprite:move-to-cursor-display',
    updateCheck: 'spiritagent:update:check'
  } as const satisfies Record<string, IpcChannel>,
  event: {
    authChanged: 'spiritagent:auth:changed',
    authSessionExpired: 'spiritagent:auth:session-expired',
    bootProgress: 'spiritagent:boot-progress',
    powerResume: 'spiritagent:power-resume',
    runnerStatus: 'spiritagent:runner:status',
    trayActivate: 'spiritagent:tray:activate',
    trayLogout: 'spiritagent:tray:logout',
    trayOpenChat: 'spiritagent:tray:open-chat',
    uiThemeChanged: 'spiritagent:ui-theme-changed',
    updateEvent: 'spiritagent:update-event'
  } as const satisfies Record<string, IpcEventChannel>,
  send: {
    titleBarTheme: 'spiritagent:titlebar-theme',
    uiTheme: 'spiritagent:ui-theme'
  } as const satisfies Record<string, IpcSendChannel>
} as const
