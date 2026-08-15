import type {
  DeskAgentApiRequest,
  DeskAgentConnection,
  DeskAgentSelectPathsOptions,
  DeskAgentTitleBarTheme,
  DeskAgentWindowState,
  DesktopActivatePayload,
  DesktopAuthBroadcast,
  DesktopAuthSnapshot,
  DesktopBootProgress,
  DesktopLogoutResult,
  DesktopRunnerState,
  DesktopRunnerStatusEvent,
  DesktopRunnerUpdateEvent,
  DesktopUpdateEvent,
  DesktopVersionInfo
} from '../../renderer/shared/types/global'

export interface SkillItem {
  category: string
  compatible: boolean
  description?: string
  enabled: boolean
  name: string
  platforms?: string[] | null
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
  'deskagent:connection': () => Promise<DeskAgentConnection> | DeskAgentConnection
  'deskagent:gateway:ws-url': () => Promise<string> | string
  'deskagent:boot-progress:get': () => Promise<DesktopBootProgress> | DesktopBootProgress

  // Auth
  'deskagent:auth:activate': (payload: DesktopActivatePayload) => Promise<DesktopAuthSnapshot> | DesktopAuthSnapshot
  'deskagent:auth:refresh': (payload?: Record<string, unknown>) => Promise<DesktopAuthSnapshot> | DesktopAuthSnapshot
  'deskagent:auth:logout': () => Promise<DesktopLogoutResult> | DesktopLogoutResult
  'deskagent:auth:get-session': () => Promise<DesktopAuthSnapshot | null> | DesktopAuthSnapshot | null
  'deskagent:auth:get-default-backend-url': () => Promise<null | string> | null | string

  // Windows & UI
  'deskagent:window:show-tool': () => Promise<void> | void

  // Backend API Proxy
  'deskagent:api': (request: DeskAgentApiRequest) => Promise<unknown> | unknown
  'deskagent:api:asset': (request: { url: string }) => Promise<string> | string
  'deskagent:api:asset-cached-path': (request: {
    contentHash: string
    url: string
  }) => Promise<null | string> | null | string
  'deskagent:api:asset-buffer': (request: { contentHash?: string; url: string }) => Promise<Uint8Array> | Uint8Array

  // File & Clipboard & Log
  'deskagent:readFileDataUrl': (filePath: string) => Promise<string> | string
  'deskagent:selectPaths': (options?: DeskAgentSelectPathsOptions) => Promise<string[]> | string[]
  'deskagent:writeClipboard': (text: string) => Promise<boolean> | boolean
  'deskagent:saveClipboardImage': () => Promise<string> | string
  'deskagent:log:emit': (payload: {
    args: unknown[]
    level: 'error' | 'info' | 'warn'
    scope: string
  }) => Promise<void> | void
  'deskagent:version': () => Promise<DesktopVersionInfo> | DesktopVersionInfo

  // Runner
  'deskagent:runner:invoke': (name: string, args: Record<string, unknown>) => Promise<unknown> | unknown
  'deskagent:runner:reload-mcp': () => Promise<unknown> | unknown
  'deskagent:runner:get-state': () => Promise<DesktopRunnerState> | DesktopRunnerState
  'deskagent:runner:get-tools': () => Promise<Array<Record<string, unknown>>> | Array<Record<string, unknown>>
  'deskagent:runner-config:read': () =>
    | Promise<{ content?: string; error?: string; ok: boolean }>
    | { content?: string; error?: string; ok: boolean }
  'deskagent:runner-config:write': (
    configString: string
  ) => Promise<{ error?: string; ok: boolean }> | { error?: string; ok: boolean }
  'deskagent:runner-config:patch': (
    patch: RunnerConfigPatch
  ) => Promise<{ error?: string; ok: boolean }> | { error?: string; ok: boolean }

  // Skills & Toolsets
  'deskagent:skills:list': () =>
    | Promise<{ error?: string; ok: boolean; skills?: SkillItem[] }>
    | { error?: string; ok: boolean; skills?: SkillItem[] }
  'deskagent:skill:set-enabled': (payload: {
    enabled: boolean
    name: string
  }) =>
    | Promise<{ error?: string; ok: boolean; skills?: SkillItem[] }>
    | { error?: string; ok: boolean; skills?: SkillItem[] }
  'deskagent:toolsets:list': () =>
    | Promise<{ error?: string; ok: boolean; toolsets?: ToolsetItem[] }>
    | { error?: string; ok: boolean; toolsets?: ToolsetItem[] }
  'deskagent:toolset:set-enabled': (payload: {
    enabled: boolean
    id: string
  }) =>
    | Promise<{ error?: string; ok: boolean; toolsets?: ToolsetItem[] }>
    | { error?: string; ok: boolean; toolsets?: ToolsetItem[] }

  // Media
  'deskagent:media:stt': (payload: MediaSttPayload) => Promise<{ text: string }> | { text: string }
  'deskagent:media:tts': (
    payload: MediaTtsPayload
  ) => Promise<{ dataUrl: string; mimeType: string }> | { dataUrl: string; mimeType: string }
  'deskagent:onboardingAudio:read': (
    tag: string
  ) =>
    | Promise<{ bytes: number; dataUrl: string; mimeType: string; tag: string }>
    | { bytes: number; dataUrl: string; mimeType: string; tag: string }

  // Sprite
  'deskagent:sprite:set-ignore-mouse-events': (payload: { forward?: boolean; ignore: boolean }) => Promise<void> | void
  'deskagent:sprite:set-always-on-top': (payload: { on: boolean }) => Promise<void> | void
  'deskagent:sprite:get-position': () => Promise<null | { x: number; y: number }> | null | { x: number; y: number }
  'deskagent:sprite:set-position': (payload: { x: number; y: number }) => Promise<void> | void

  // Update
  'deskagent:update:check': () => Promise<void> | void
}

// 2. Events pushed Main -> Renderer (via webContents.send / ipcRenderer.on)
export interface IpcEventContract {
  'deskagent:window-state-changed': [payload: DeskAgentWindowState]
  'deskagent:power-resume': []
  'deskagent:boot-progress': [payload: DesktopBootProgress]
  'deskagent:auth:session-expired': []
  'deskagent:auth:changed': [payload: DesktopAuthBroadcast]
  'deskagent:runner:status': [payload: DesktopRunnerStatusEvent]
  'deskagent:tray:logout': []
  'deskagent:update-event': [payload: DesktopUpdateEvent]
  'deskagent:runner-update-event': [payload: DesktopRunnerUpdateEvent]
}

// 3. Unidirectional messages Renderer -> Main (via ipcRenderer.send / ipcMain.on)
export interface IpcSendContract {
  'deskagent:titlebar-theme': [payload: DeskAgentTitleBarTheme]
}

export type IpcChannel = keyof IpcInvokeContract
export type IpcEventChannel = keyof IpcEventContract
export type IpcSendChannel = keyof IpcSendContract
