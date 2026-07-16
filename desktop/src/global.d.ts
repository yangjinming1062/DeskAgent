export {}

declare global {
  interface Window {
    zastDesktop: {
      getConnection: () => Promise<ZastConnection>
      getGatewayWsUrl: () => Promise<string>
      getBootProgress: () => Promise<DesktopBootProgress>
      login: (payload: DesktopLoginPayload) => Promise<DesktopAuthSnapshot>
      refreshSession: (payload?: any) => Promise<DesktopAuthSnapshot>
      logout: () => Promise<DesktopLogoutResult>
      getSession: () => Promise<DesktopAuthSnapshot | null>
      api: <T>(request: ZastApiRequest) => Promise<T>
      notify: (payload: ZastNotification) => Promise<boolean>
      requestMicrophoneAccess: () => Promise<boolean>
      readFileDataUrl: (filePath: string) => Promise<string>
      readFileText: (filePath: string) => Promise<ZastReadFileTextResult>
      selectPaths: (options?: ZastSelectPathsOptions) => Promise<string[]>
      writeClipboard: (text: string) => Promise<boolean>
      saveImageFromUrl: (url: string) => Promise<boolean>
      saveImageBuffer: (data: ArrayBuffer | Uint8Array, ext: string) => Promise<string>
      saveClipboardImage: () => Promise<string>
      runnerInvoke?: (name: string, args: Record<string, unknown>) => Promise<unknown>
      runnerDispatch?: (method: string, params?: Record<string, unknown>) => Promise<unknown>
      runnerGetTools?: () => Promise<Array<Record<string, unknown>>>
      getPathForFile: (file: File) => string
      normalizePreviewTarget: (target: string, baseDir?: string) => Promise<ZastPreviewTarget | null>
      watchPreviewFile: (url: string) => Promise<ZastPreviewWatch>
      stopPreviewFileWatch: (id: string) => Promise<boolean>
      setTitleBarTheme?: (payload: ZastTitleBarTheme) => void
      setPreviewShortcutActive?: (active: boolean) => void
      openExternal: (url: string) => Promise<void>
      fetchLinkTitle: (url: string) => Promise<string>
      settings: {
        getDefaultProjectDir: () => Promise<{ defaultLabel: string; dir: null | string }>
        pickDefaultProjectDir: () => Promise<{ canceled: boolean; dir: null | string }>
        setDefaultProjectDir: (dir: null | string) => Promise<{ dir: null | string }>
      }
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
      readDir: (path: string) => Promise<ZastReadDirResult>
      gitRoot?: (path: string) => Promise<string | null>
      gitBranch?: (path: string) => Promise<{ branch: string; root: string | null }>
      completePath?: (params: { word: string; cwd?: string }) => Promise<{
        items: Array<{ label: string; value: string; isDirectory: boolean }>
      }>
      changePassword: (payload: {
        current_password: string
        new_password: string
      }) => Promise<{ ok: boolean; message?: string }>
      modelConfig: {
        get: () => Promise<{
          llm_model_name: string
          llm_base_url: string
          llm_api_key_fingerprint: string
          llm_api_key_set: boolean
        }>
      }
      media: {
        stt: (payload: { dataUrl: string; filename?: string }) => Promise<{ text: string }>
        tts: (payload: { text: string; voice?: string }) => Promise<{ dataUrl: string; mimeType: string }>
      }
      terminal: {
        dispose: (id: string) => Promise<boolean>
        onData: (id: string, callback: (payload: string) => void) => () => void
        onExit: (id: string, callback: (payload: ZastTerminalExit) => void) => () => void
        resize: (id: string, size: { cols: number; rows: number }) => Promise<boolean>
        start: (options?: { cols?: number; cwd?: string; rows?: number }) => Promise<ZastTerminalSession>
        write: (id: string, data: string) => Promise<boolean>
      }
      onClosePreviewRequested?: (callback: () => void) => () => void
      onWindowStateChanged?: (callback: (payload: ZastWindowState) => void) => () => void
      onPreviewFileChanged: (callback: (payload: ZastPreviewFileChanged) => void) => () => void
      onPowerResume?: (callback: () => void) => () => void
      onBootProgress: (callback: (payload: DesktopBootProgress) => void) => () => void
      onSessionExpired: (callback: () => void) => () => void
      onRunnerStatus?: (callback: (payload: DesktopRunnerStatusEvent) => void) => () => void
      getVersion: () => Promise<DesktopVersionInfo>
      update?: {
        check: () => Promise<{ ok: boolean; reason?: string }>
        download: () => Promise<{ ok: boolean; reason?: string }>
        install: () => Promise<{ ok: boolean; reason?: string }>
        status: () => Promise<{ currentVersion: string } | { ok: false; reason: string }>
        retryRunnerInstall: () => Promise<{ ok: boolean; noop?: boolean; error?: string }>
        onEvent: (callback: (payload: DesktopUpdateEvent) => void) => () => void
        onRunnerEvent: (callback: (payload: DesktopRunnerUpdateEvent) => void) => () => void
      }
      recorder?: {
        startWithToolbar?: () => Promise<boolean>
        pause?: () => Promise<boolean>
        resume?: () => Promise<boolean>
        stop?: () => Promise<boolean>
        setData?: (data: ArrayBuffer) => Promise<boolean>
        finishUpload?: () => Promise<string>
        hideToolbar?: () => Promise<boolean>
        moveToolbar?: (pos: { x: number; y: number }) => Promise<boolean>
        onFinished?: (callback: (uri: string) => void) => () => void
        onStopped?: (callback: () => void) => () => void
        onUploadStarted?: (callback: () => void) => () => void
        onProgress?: (callback: (payload: RecorderProgressPayload) => void) => () => void
        onFailed?: (callback: (payload: { message: string }) => void) => () => void
      }
    }
  }
}

export interface ZastTerminalSession {
  cwd: string
  id: string
  shell: string
}

export interface ZastTerminalExit {
  code: number | null
  signal: string | null
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
// on the `zast:runner-update-event` IPC channel. Phase 1 (prefetch) runs in
// the OLD Electron after `update-downloaded`; phase 2 (install) runs in the
// NEW Electron at startup. `recoverable: false` means the user must reinstall.
export type DesktopRunnerUpdateEvent =
  | { kind: 'runner-prefetching'; version: string; phase: 'manifest' | 'wheel' | 'server'; percent?: number }
  | { kind: 'runner-ready'; version: string }
  | { kind: 'runner-installing'; version: string; phase: 'pip' | 'starting'; percent?: number }
  | { kind: 'runner-installed'; version: string }
  | { kind: 'runner-failed'; error: string; recoverable: boolean; version?: string }

// Runner lifecycle events from runner-bridge.cjs (`running` / `stopped` /
// `error` / `tools_changed`), forwarded over the `zast:runner:status` IPC
// channel. Renderer subscribes via `onRunnerStatus`; see use-gateway-boot.ts
// where the `running` and `tools_changed` variants both trigger a
// tool-schema sync to backend.
export type DesktopRunnerStatusEvent =
  | { type: 'running'; tools: unknown[] }
  | { type: 'tools_changed'; tools: unknown[] }
  | { type: 'stopped'; reason?: string; errors: string[] }
  | { type: 'error'; phase: string; error: Error }

// Recorder upload progress payload, broadcast every ~100ms during the
// Desktop → Backend HTTP upload (XHR `upload.onprogress`, throttled). The
// renderer's toolbar / composer subscribe to show a live progress bar.
export interface RecorderProgressPayload {
  bytesSent: number
  totalBytes: number
  percent: number
  bytesPerSec: number
  etaMs: number | null
}

export interface ZastConnection {
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

export interface ZastTitleBarTheme {
  background: string
  foreground: string
}

export interface ZastWindowState {
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
  display_name: string
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
}

export interface DesktopLogoutResult {
  backendUnreachable?: boolean
  error?: string
  ok: boolean
}

export interface ZastApiRequest {
  body?: unknown
  method?: string
  path: string
  timeoutMs?: number
}

export interface ZastNotification {
  body?: string
  silent?: boolean
  title?: string
}

export interface ZastPreviewTarget {
  binary?: boolean
  byteSize?: number
  kind: 'file' | 'url'
  label: string
  large?: boolean
  language?: string
  mimeType?: string
  path?: string
  previewKind?: 'binary' | 'html' | 'image' | 'text'
  renderMode?: 'preview' | 'source'
  source: string
  url: string
}

export interface ZastReadFileTextResult {
  binary?: boolean
  byteSize?: number
  language?: string
  mimeType?: string
  path: string
  text: string
  truncated?: boolean
}

export interface ZastPreviewWatch {
  id: string
  path: string
}

export interface ZastReadDirEntry {
  isDirectory: boolean
  name: string
  path: string
}

export interface ZastReadDirResult {
  entries: ZastReadDirEntry[]
  error?: string
}

export interface ZastPreviewFileChanged {
  id: string
  path: string
  url: string
}

export interface ZastSelectPathsOptions {
  defaultPath?: string
  directories?: boolean
  filters?: Array<{ extensions: string[]; name: string }>
  multiple?: boolean
  title?: string
}
