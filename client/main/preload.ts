import { contextBridge, ipcRenderer, type IpcRendererEvent, webUtils } from 'electron'

import type {
  DesktopActivatePayload,
  DesktopAuthBroadcast,
  DesktopBootProgress,
  DesktopRunnerStatusEvent,
  DesktopRunnerUpdateEvent,
  DesktopUpdateEvent,
  MediaSttPayload,
  MediaTtsPayload,
  RunnerConfigPatch,
  SpiritAgentApiRequest,
  SpiritAgentSelectPathsOptions,
  SpiritAgentTitleBarTheme,
  SpiritAgentWindowState
} from './shared/ipc-contracts'

contextBridge.exposeInMainWorld('spiritagent', {
  activate: (payload: DesktopActivatePayload) => ipcRenderer.invoke('spiritagent:auth:activate', payload),
  api: (request: SpiritAgentApiRequest) => ipcRenderer.invoke('spiritagent:api', request),
  apiAsset: (request: { url: string }) => ipcRenderer.invoke('spiritagent:api:asset', request),
  apiAssetBuffer: (request: { contentHash?: string; url: string }) =>
    ipcRenderer.invoke('spiritagent:api:asset-buffer', request),
  getBootProgress: () => ipcRenderer.invoke('spiritagent:boot-progress:get'),
  getConnection: () => ipcRenderer.invoke('spiritagent:connection'),
  getDefaultBackendUrl: () => ipcRenderer.invoke('spiritagent:auth:get-default-backend-url'),
  getGatewayWsUrl: () => ipcRenderer.invoke('spiritagent:gateway:ws-url'),
  getPathForFile: (file: File): string => {
    try {
      return webUtils.getPathForFile(file) || ''
    } catch {
      return ''
    }
  },
  getSession: () => ipcRenderer.invoke('spiritagent:auth:get-session'),
  getVersion: () => ipcRenderer.invoke('spiritagent:version'),
  log: (payload: { args: unknown[]; level: 'error' | 'info' | 'warn'; scope: string }) =>
    ipcRenderer.invoke('spiritagent:log:emit', payload),
  logout: () => ipcRenderer.invoke('spiritagent:auth:logout'),
  media: {
    onboardingAudio: {
      read: (tag: string) => ipcRenderer.invoke('spiritagent:onboardingAudio:read', tag)
    },
    stt: (payload: MediaSttPayload) => ipcRenderer.invoke('spiritagent:media:stt', payload),
    tts: (payload: MediaTtsPayload) => ipcRenderer.invoke('spiritagent:media:tts', payload)
  },
  onAuthChanged: (callback: (payload: DesktopAuthBroadcast) => void) => {
    const listener = (_event: IpcRendererEvent, payload: DesktopAuthBroadcast) => callback(payload)
    ipcRenderer.on('spiritagent:auth:changed', listener)

    return () => ipcRenderer.removeListener('spiritagent:auth:changed', listener)
  },
  onBootProgress: (callback: (payload: DesktopBootProgress) => void) => {
    const listener = (_event: IpcRendererEvent, payload: DesktopBootProgress) => callback(payload)
    ipcRenderer.on('spiritagent:boot-progress', listener)

    return () => ipcRenderer.removeListener('spiritagent:boot-progress', listener)
  },
  onPowerResume: (callback: () => void) => {
    const listener = () => callback()
    ipcRenderer.on('spiritagent:power-resume', listener)

    return () => ipcRenderer.removeListener('spiritagent:power-resume', listener)
  },
  onRunnerStatus: (callback: (payload: DesktopRunnerStatusEvent) => void) => {
    const listener = (_event: IpcRendererEvent, payload: DesktopRunnerStatusEvent) => callback(payload)
    ipcRenderer.on('spiritagent:runner:status', listener)

    return () => ipcRenderer.removeListener('spiritagent:runner:status', listener)
  },
  onSessionExpired: (callback: () => void) => {
    const listener = () => callback()
    ipcRenderer.on('spiritagent:auth:session-expired', listener)

    return () => ipcRenderer.removeListener('spiritagent:auth:session-expired', listener)
  },
  onTrayLogout: (callback: () => void) => {
    const listener = () => callback()
    ipcRenderer.on('spiritagent:tray:logout', listener)

    return () => ipcRenderer.removeListener('spiritagent:tray:logout', listener)
  },
  onWindowStateChanged: (callback: (payload: SpiritAgentWindowState) => void) => {
    const listener = (_event: IpcRendererEvent, payload: SpiritAgentWindowState) => callback(payload)
    ipcRenderer.on('spiritagent:window-state-changed', listener)

    return () => ipcRenderer.removeListener('spiritagent:window-state-changed', listener)
  },
  readFileDataUrl: (filePath: string) => ipcRenderer.invoke('spiritagent:readFileDataUrl', filePath),
  refreshSession: (payload?: Record<string, unknown>) => ipcRenderer.invoke('spiritagent:auth:refresh', payload),
  reloadMcp: () => ipcRenderer.invoke('spiritagent:runner:reload-mcp'),
  runnerCancel: () => ipcRenderer.invoke('spiritagent:runner:cancel'),
  runnerConfig: {
    patch: (patch: RunnerConfigPatch) => ipcRenderer.invoke('spiritagent:runner-config:patch', patch),
    read: () => ipcRenderer.invoke('spiritagent:runner-config:read'),
    write: (configString: string) => ipcRenderer.invoke('spiritagent:runner-config:write', configString)
  },
  runnerGetState: () => ipcRenderer.invoke('spiritagent:runner:get-state'),
  runnerGetTools: () => ipcRenderer.invoke('spiritagent:runner:get-tools'),
  runnerInvoke: (name: string, args?: Record<string, unknown>) =>
    ipcRenderer.invoke('spiritagent:runner:invoke', name, args || {}),
  saveClipboardImage: () => ipcRenderer.invoke('spiritagent:saveClipboardImage'),
  selectPaths: (options?: SpiritAgentSelectPathsOptions) => ipcRenderer.invoke('spiritagent:selectPaths', options),
  setTitleBarTheme: (payload: SpiritAgentTitleBarTheme) => ipcRenderer.send('spiritagent:titlebar-theme', payload),
  showToolWindow: () => ipcRenderer.invoke('spiritagent:window:show-tool'),
  skills: {
    list: () => ipcRenderer.invoke('spiritagent:skills:list'),
    setEnabled: (payload: { enabled: boolean; name: string }) =>
      ipcRenderer.invoke('spiritagent:skill:set-enabled', payload)
  },
  sprite: {
    getPosition: () => ipcRenderer.invoke('spiritagent:sprite:get-position'),
    moveToCursorDisplay: () => ipcRenderer.invoke('spiritagent:sprite:move-to-cursor-display'),
    setAlwaysOnTop: (payload: { on: boolean }) => ipcRenderer.invoke('spiritagent:sprite:set-always-on-top', payload),
    setIgnoreMouseEvents: (payload: { forward?: boolean; ignore: boolean }) =>
      ipcRenderer.invoke('spiritagent:sprite:set-ignore-mouse-events', payload),
    setPosition: (payload: { x: number; y: number }) => ipcRenderer.invoke('spiritagent:sprite:set-position', payload)
  },
  toolsets: {
    list: () => ipcRenderer.invoke('spiritagent:toolsets:list'),
    setEnabled: (payload: { enabled: boolean; id: string }) =>
      ipcRenderer.invoke('spiritagent:toolset:set-enabled', payload)
  },
  update: {
    check: () => ipcRenderer.invoke('spiritagent:update:check'),
    onEvent: (callback: (payload: DesktopUpdateEvent) => void) => {
      const listener = (_event: IpcRendererEvent, payload: DesktopUpdateEvent) => callback(payload)
      ipcRenderer.on('spiritagent:update-event', listener)

      return () => ipcRenderer.removeListener('spiritagent:update-event', listener)
    },
    onRunnerEvent: (callback: (payload: DesktopRunnerUpdateEvent) => void) => {
      const listener = (_event: IpcRendererEvent, payload: DesktopRunnerUpdateEvent) => callback(payload)
      ipcRenderer.on('spiritagent:runner-update-event', listener)

      return () => ipcRenderer.removeListener('spiritagent:runner-update-event', listener)
    }
  },
  writeClipboard: (text: string) => ipcRenderer.invoke('spiritagent:writeClipboard', text)
})
