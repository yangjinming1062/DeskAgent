export {}

declare global {
  interface Window {
    spiritagent: {
      getConnection: () => Promise<SpiritAgentConnection>
      getGatewayWsUrl: () => Promise<string>
      getBootProgress: () => Promise<DesktopBootProgress>
      activate: (payload: DesktopActivatePayload) => Promise<DesktopAuthSnapshot>
      refreshSession: (payload?: Record<string, unknown>) => Promise<DesktopAuthSnapshot>
      logout: () => Promise<DesktopLogoutResult>
      getSession: () => Promise<DesktopAuthSnapshot | null>
      showToolWindow: () => Promise<void>
      api: <T>(request: SpiritAgentApiRequest) => Promise<T>
      /** 把后端服务的二进制资产以 data URL 的形式取回（见 connection.cjs）。 */
      apiAsset: (request: { url: string }) => Promise<string>
      /** 把后端服务的二进制资产以原始字节取回——用于大体积负载（GLB），
       * 不能接受 base64 膨胀。支持通过 contentHash 做磁盘缓存。 */
      apiAssetBuffer: (request: { url: string; contentHash?: string }) => Promise<Uint8Array>
      readFileDataUrl: (filePath: string) => Promise<string>
      selectPaths: (options?: SpiritAgentSelectPathsOptions) => Promise<string[]>
      writeClipboard: (text: string) => Promise<boolean>
      saveClipboardImage: () => Promise<string>
      log: (payload: { level: 'error' | 'info' | 'warn'; scope: string; args: unknown[] }) => Promise<void>
      runnerInvoke?: (name: string, args: Record<string, unknown>) => Promise<unknown>
      runnerCancel?: () => Promise<unknown>
      reloadMcp: () => Promise<unknown>
      runnerGetState?: () => Promise<DesktopRunnerState>
      runnerGetTools?: () => Promise<Array<Record<string, unknown>>>
      setTitleBarTheme?: (payload: SpiritAgentTitleBarTheme) => void
      runnerConfig: {
        read: () => Promise<{ ok: boolean; content?: string; error?: string }>
        write: (configString: string) => Promise<{ ok: boolean; error?: string }>
        patch: (patch: {
          path: readonly (string | number)[]
          value?: unknown
          op?: 'set' | 'delete'
        }) => Promise<{ ok: boolean; error?: string }>
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
      media: {
        stt: (payload: {
          context?: string | null
          dataUrl: string
          filename?: string
          language?: string
        }) => Promise<{ text: string }>
        tts: (payload: {
          context?: string | null
          persist?: boolean
          text: string
          voice?: string
        }) => Promise<{ dataUrl: string; mimeType: string }>
        onboardingAudio: {
          read: (tag: string) => Promise<{ dataUrl: string; mimeType: string; tag: string; bytes: number }>
        }
      }
      sprite: {
        hide: () => Promise<void>
        setIgnoreMouseEvents: (payload: { ignore: boolean; forward?: boolean }) => Promise<void>
        setAlwaysOnTop: (payload: { on: boolean }) => Promise<void>
        getPosition: () => Promise<{ origin?: { x: number; y: number }; x: number; y: number } | null>
        moveToCursorDisplay: () => Promise<{
          cursor: { x: number; y: number }
          from: { x: number; y: number }
          to: { x: number; y: number }
        } | null>
        setPosition: (payload: { x: number; y: number }) => Promise<void>
      }
      onWindowStateChanged?: (callback: (payload: SpiritAgentWindowState) => void) => () => void
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

// Runner 侧的更新事件，由 main.cjs → runner-updater.cjs 经 `spiritagent:runner-update-event`
// IPC 通道转发。阶段 1（预取）在旧的 Electron 中、收到 `update-downloaded` 之后运行；
// 阶段 2（安装）在新的 Electron 启动时运行。`recoverable: false` 表示用户必须重装。
export type DesktopRunnerUpdateEvent =
  | { kind: 'runner-prefetching'; version: string; phase: 'manifest' | 'wheel' | 'server'; percent?: number }
  | { kind: 'runner-ready'; version: string }
  | { kind: 'runner-installing'; version: string; phase: 'pip' | 'starting'; percent?: number }
  | { kind: 'runner-installed'; version: string }
  | { kind: 'runner-failed'; error: string; recoverable: boolean; version?: string }

// Runner 能力探测结果。来自 `runner_ready` 事件、
export interface CapabilityHealthItem {
  available: boolean
  reason?: string | null
}

export type RunnerCapabilitiesHealth = Record<string, CapabilityHealthItem>

// runner-bridge.cjs 中的 `running` / `tools_changed` 生命周期变体——
// 每个能力对应一个真实的、按平台的子体探测
// （sounddevice、Win32 GetLastInputInfo、Quartz、loginctl，……）。
export interface RunnerCapabilities {
  microphone?: boolean
  screen_capture?: boolean
  local_stt?: boolean
  local_tts?: boolean
  system_activity?: boolean
  platform?: string
  python?: string
}

// 来自 runner-bridge.cjs 的 Runner 生命周期事件（`running` / `stopped` /
// `error` / `tools_changed`），经 `spiritagent:runner:status` IPC 通道转发。
// 渲染层通过 `onRunnerStatus` 订阅；见 use-gateway-boot.ts，
// 其中 `running` 与 `tools_changed` 两个变体都会触发一次向后端的工具 schema 同步。
export type DesktopRunnerStatusEvent =
  | {
      type: 'running'
      tools: unknown[]
      capabilities?: RunnerCapabilities | null
      capabilitiesHealth?: RunnerCapabilitiesHealth | null
      runnerVersion?: string | null
      probeFailed?: boolean | null
    }
  | {
      type: 'runner_ready'
      capabilities?: RunnerCapabilities | null
      capabilitiesHealth?: RunnerCapabilitiesHealth | null
      runnerVersion?: string | null
      probeFailed?: boolean | null
    }
  | {
      type: 'tools_changed'
      tools: unknown[]
      capabilities?: RunnerCapabilities | null
      capabilitiesHealth?: RunnerCapabilitiesHealth | null
    }
  | { type: 'stopped'; reason?: string; errors: string[] }
  | { type: 'error'; phase: string; error: Error }

// Runner 网桥生命周期的同步快照。由 ``runnerGetState`` 返回；
// 后续转换由 ``DesktopRunnerStatusEvent`` 配合给出。
// 与 runner-bridge.cjs 派发的 phase 取值一一对应。
export type DesktopRunnerPhase = 'idle' | 'starting' | 'running' | 'stopping' | 'stopped' | 'error'

export interface DesktopRunnerState {
  phase: DesktopRunnerPhase
  startedAt?: number | null
  stoppedAt?: number | null
  lastError?: string | null
  capabilities?: RunnerCapabilities | null
  capabilitiesHealth?: RunnerCapabilitiesHealth | null
  runnerVersion?: string | null
  probeFailed?: boolean | null
}

export interface SpiritAgentConnection {
  baseUrl: string
  isFullscreen: boolean
  mode?: 'local' | 'remote'
  nativeOverlayWidth: number
  source?: 'env' | 'local' | 'settings'
  token: null | string
  wsUrl: string
  logs: string[]
  windowButtonPosition: { x: number; y: number } | null
}

export interface SpiritAgentTitleBarTheme {
  background: string
  foreground: string
}

export interface SpiritAgentWindowState {
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

export interface DesktopActivatePayload {
  code: string
}

export interface DesktopLogoutResult {
  backendUnreachable?: boolean
  error?: string
  ok: boolean
}

// 主进程 → 渲染层在每次登录 / 登出 / 刷新后的广播，同时发送给两个窗口，
// 以保证各渲染层的每窗口 $auth 保持同步。精灵窗口从不展示登录界面，
// 因此依赖此广播来感知新会话并启停自己的网关。
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
