import {
  type DesktopActivatePayload,
  type DesktopAuthBroadcast,
  type DesktopBootProgress,
  type DesktopRunnerStatusEvent,
  type DesktopRunnerUpdateEvent,
  type DesktopUpdateEvent,
  IPC,
  type MediaSttPayload,
  type MediaTtsPayload,
  type RunnerConfigPatch,
  type SpiritAgentApiRequest,
  type SpiritAgentSelectPathsOptions,
  type SpiritAgentTitleBarTheme,
  type SpiritAgentWindowState
} from '@ipc/contracts'
import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron'

contextBridge.exposeInMainWorld('spiritagent', {
  activate: (payload: DesktopActivatePayload) => ipcRenderer.invoke(IPC.invoke.authActivate, payload),
  api: (request: SpiritAgentApiRequest) => ipcRenderer.invoke(IPC.invoke.api, request),
  apiAsset: (request: { url: string }) => ipcRenderer.invoke(IPC.invoke.apiAsset, request),
  apiAssetBuffer: (request: { contentHash?: string; url: string }) =>
    ipcRenderer.invoke(IPC.invoke.apiAssetBuffer, request),
  apiAssetModelUrl: (request: { contentHash?: string; url: string }) =>
    ipcRenderer.invoke(IPC.invoke.apiAssetModelUrl, request),
  getBootProgress: () => ipcRenderer.invoke(IPC.invoke.bootProgressGet),
  getConnection: () => ipcRenderer.invoke(IPC.invoke.connection),
  getGatewayWsUrl: () => ipcRenderer.invoke(IPC.invoke.gatewayWsUrl),
  getSession: () => ipcRenderer.invoke(IPC.invoke.authGetSession),
  getVersion: () => ipcRenderer.invoke(IPC.invoke.version),
  log: (payload: { args: unknown[]; level: 'error' | 'info' | 'warn'; scope: string }) =>
    ipcRenderer.invoke(IPC.invoke.logEmit, payload),
  logout: () => ipcRenderer.invoke(IPC.invoke.authLogout),
  media: {
    onboardingAudio: {
      read: (tag: string) => ipcRenderer.invoke(IPC.invoke.onboardingAudioRead, tag)
    },
    stt: (payload: MediaSttPayload) => ipcRenderer.invoke(IPC.invoke.mediaStt, payload),
    tts: (payload: MediaTtsPayload) => ipcRenderer.invoke(IPC.invoke.mediaTts, payload)
  },
  onAuthChanged: (callback: (payload: DesktopAuthBroadcast) => void) => {
    const listener = (_event: IpcRendererEvent, payload: DesktopAuthBroadcast) => callback(payload)
    ipcRenderer.on(IPC.event.authChanged, listener)

    return () => ipcRenderer.removeListener(IPC.event.authChanged, listener)
  },
  onBootProgress: (callback: (payload: DesktopBootProgress) => void) => {
    const listener = (_event: IpcRendererEvent, payload: DesktopBootProgress) => callback(payload)
    ipcRenderer.on(IPC.event.bootProgress, listener)

    return () => ipcRenderer.removeListener(IPC.event.bootProgress, listener)
  },
  onPowerResume: (callback: () => void) => {
    const listener = () => callback()
    ipcRenderer.on(IPC.event.powerResume, listener)

    return () => ipcRenderer.removeListener(IPC.event.powerResume, listener)
  },
  onRunnerStatus: (callback: (payload: DesktopRunnerStatusEvent) => void) => {
    const listener = (_event: IpcRendererEvent, payload: DesktopRunnerStatusEvent) => callback(payload)
    ipcRenderer.on(IPC.event.runnerStatus, listener)

    return () => ipcRenderer.removeListener(IPC.event.runnerStatus, listener)
  },
  onSessionExpired: (callback: () => void) => {
    const listener = () => callback()
    ipcRenderer.on(IPC.event.authSessionExpired, listener)

    return () => ipcRenderer.removeListener(IPC.event.authSessionExpired, listener)
  },
  onTrayLogout: (callback: () => void) => {
    const listener = () => callback()
    ipcRenderer.on(IPC.event.trayLogout, listener)

    return () => ipcRenderer.removeListener(IPC.event.trayLogout, listener)
  },
  onWindowStateChanged: (callback: (payload: SpiritAgentWindowState) => void) => {
    const listener = (_event: IpcRendererEvent, payload: SpiritAgentWindowState) => callback(payload)
    ipcRenderer.on(IPC.event.windowStateChanged, listener)

    return () => ipcRenderer.removeListener(IPC.event.windowStateChanged, listener)
  },
  readFileDataUrl: (filePath: string) => ipcRenderer.invoke(IPC.invoke.readFileDataUrl, filePath),
  refreshSession: (payload?: Record<string, unknown>) => ipcRenderer.invoke(IPC.invoke.authRefresh, payload),
  runnerCancel: () => ipcRenderer.invoke(IPC.invoke.runnerCancel),
  runnerConfig: {
    patch: (patch: RunnerConfigPatch) => ipcRenderer.invoke(IPC.invoke.runnerConfigPatch, patch),
    read: () => ipcRenderer.invoke(IPC.invoke.runnerConfigRead),
    write: (configString: string) => ipcRenderer.invoke(IPC.invoke.runnerConfigWrite, configString)
  },
  runnerGetState: () => ipcRenderer.invoke(IPC.invoke.runnerGetState),
  runnerGetTools: () => ipcRenderer.invoke(IPC.invoke.runnerGetTools),
  runnerInvoke: (name: string, args: Record<string, unknown>) =>
    ipcRenderer.invoke(IPC.invoke.runnerInvoke, name, args),
  saveClipboardImage: () => ipcRenderer.invoke(IPC.invoke.saveClipboardImage),
  selectPaths: (options?: SpiritAgentSelectPathsOptions) => ipcRenderer.invoke(IPC.invoke.selectPaths, options),
  setTitleBarTheme: (payload: SpiritAgentTitleBarTheme) => ipcRenderer.send(IPC.send.titleBarTheme, payload),
  showToolWindow: () => ipcRenderer.invoke(IPC.invoke.windowShowTool),
  skills: {
    list: () => ipcRenderer.invoke(IPC.invoke.skillsList),
    setEnabled: (payload: { enabled: boolean; name: string }) => ipcRenderer.invoke(IPC.invoke.skillSetEnabled, payload)
  },
  sprite: {
    getPosition: () => ipcRenderer.invoke(IPC.invoke.spriteGetPosition),
    hide: () => ipcRenderer.invoke(IPC.invoke.spriteHide),
    moveToCursorDisplay: () => ipcRenderer.invoke(IPC.invoke.spriteMoveToCursorDisplay),
    setAlwaysOnTop: (payload: { on: boolean }) => ipcRenderer.invoke(IPC.invoke.spriteSetAlwaysOnTop, payload),
    setIgnoreMouseEvents: (payload: { forward?: boolean; ignore: boolean }) =>
      ipcRenderer.invoke(IPC.invoke.spriteSetIgnoreMouseEvents, payload),
    setPosition: (payload: { x: number; y: number }) => ipcRenderer.invoke(IPC.invoke.spriteSetPosition, payload)
  },
  toolsets: {
    list: () => ipcRenderer.invoke(IPC.invoke.toolsetsList),
    setEnabled: (payload: { enabled: boolean; id: string }) => ipcRenderer.invoke(IPC.invoke.toolsetSetEnabled, payload)
  },
  update: {
    check: () => ipcRenderer.invoke(IPC.invoke.updateCheck),
    onEvent: (callback: (payload: DesktopUpdateEvent) => void) => {
      const listener = (_event: IpcRendererEvent, payload: DesktopUpdateEvent) => callback(payload)
      ipcRenderer.on(IPC.event.updateEvent, listener)

      return () => ipcRenderer.removeListener(IPC.event.updateEvent, listener)
    },
    onRunnerEvent: (callback: (payload: DesktopRunnerUpdateEvent) => void) => {
      const listener = (_event: IpcRendererEvent, payload: DesktopRunnerUpdateEvent) => callback(payload)
      ipcRenderer.on(IPC.event.runnerUpdateEvent, listener)

      return () => ipcRenderer.removeListener(IPC.event.runnerUpdateEvent, listener)
    }
  },
  writeClipboard: (text: string) => ipcRenderer.invoke(IPC.invoke.writeClipboard, text)
})
