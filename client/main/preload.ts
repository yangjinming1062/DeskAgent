import { contextBridge, ipcRenderer, webUtils } from 'electron'

contextBridge.exposeInMainWorld('deskagent', {
  activate: (payload: any) => ipcRenderer.invoke('deskagent:auth:activate', payload),
  api: (request: any) => ipcRenderer.invoke('deskagent:api', request),
  apiAsset: (request: any) => ipcRenderer.invoke('deskagent:api:asset', request),
  apiAssetBuffer: (request: any) => ipcRenderer.invoke('deskagent:api:asset-buffer', request),
  getBootProgress: () => ipcRenderer.invoke('deskagent:boot-progress:get'),
  getConnection: () => ipcRenderer.invoke('deskagent:connection'),
  getDefaultBackendUrl: () => ipcRenderer.invoke('deskagent:auth:get-default-backend-url'),
  getGatewayWsUrl: () => ipcRenderer.invoke('deskagent:gateway:ws-url'),
  getPathForFile: (file: File) => {
    try {
      return webUtils.getPathForFile(file) || ''
    } catch {
      return ''
    }
  },
  getSession: () => ipcRenderer.invoke('deskagent:auth:get-session'),
  getVersion: () => ipcRenderer.invoke('deskagent:version'),
  log: (payload: any) => ipcRenderer.invoke('deskagent:log:emit', payload),
  logout: () => ipcRenderer.invoke('deskagent:auth:logout'),
  media: {
    onboardingAudio: {
      read: (tag: string) => ipcRenderer.invoke('deskagent:onboardingAudio:read', tag)
    },
    stt: (payload: any) => ipcRenderer.invoke('deskagent:media:stt', payload),
    tts: (payload: any) => ipcRenderer.invoke('deskagent:media:tts', payload)
  },
  onAuthChanged: (callback: (payload: any) => void) => {
    const listener = (_event: any, payload: any) => callback(payload)
    ipcRenderer.on('deskagent:auth:changed', listener)

    return () => ipcRenderer.removeListener('deskagent:auth:changed', listener)
  },
  onBootProgress: (callback: (payload: any) => void) => {
    const listener = (_event: any, payload: any) => callback(payload)
    ipcRenderer.on('deskagent:boot-progress', listener)

    return () => ipcRenderer.removeListener('deskagent:boot-progress', listener)
  },
  onPowerResume: (callback: () => void) => {
    const listener = () => callback()
    ipcRenderer.on('deskagent:power-resume', listener)

    return () => ipcRenderer.removeListener('deskagent:power-resume', listener)
  },
  onRunnerStatus: (callback: (payload: any) => void) => {
    const listener = (_event: any, payload: any) => callback(payload)
    ipcRenderer.on('deskagent:runner:status', listener)

    return () => ipcRenderer.removeListener('deskagent:runner:status', listener)
  },
  onSessionExpired: (callback: () => void) => {
    const listener = () => callback()
    ipcRenderer.on('deskagent:auth:session-expired', listener)

    return () => ipcRenderer.removeListener('deskagent:auth:session-expired', listener)
  },
  onTrayLogout: (callback: () => void) => {
    const listener = () => callback()
    ipcRenderer.on('deskagent:tray:logout', listener)

    return () => ipcRenderer.removeListener('deskagent:tray:logout', listener)
  },
  onWindowStateChanged: (callback: (payload: any) => void) => {
    const listener = (_event: any, payload: any) => callback(payload)
    ipcRenderer.on('deskagent:window-state-changed', listener)

    return () => ipcRenderer.removeListener('deskagent:window-state-changed', listener)
  },
  readFileDataUrl: (filePath: string) => ipcRenderer.invoke('deskagent:readFileDataUrl', filePath),
  refreshSession: (payload?: any) => ipcRenderer.invoke('deskagent:auth:refresh', payload),
  reloadMcp: () => ipcRenderer.invoke('deskagent:runner:reload-mcp'),
  runnerCancel: () => ipcRenderer.invoke('deskagent:runner:cancel'),
  runnerConfig: {
    patch: (patch: any) => ipcRenderer.invoke('deskagent:runner-config:patch', patch),
    read: () => ipcRenderer.invoke('deskagent:runner-config:read'),
    write: (configString: string) => ipcRenderer.invoke('deskagent:runner-config:write', configString)
  },
  runnerGetState: () => ipcRenderer.invoke('deskagent:runner:get-state'),
  runnerGetTools: () => ipcRenderer.invoke('deskagent:runner:get-tools'),
  runnerInvoke: (name: string, args: any) => ipcRenderer.invoke('deskagent:runner:invoke', name, args),
  saveClipboardImage: () => ipcRenderer.invoke('deskagent:saveClipboardImage'),
  selectPaths: (options?: any) => ipcRenderer.invoke('deskagent:selectPaths', options),
  setTitleBarTheme: (payload: any) => ipcRenderer.send('deskagent:titlebar-theme', payload),
  showToolWindow: () => ipcRenderer.invoke('deskagent:window:show-tool'),
  skills: {
    list: () => ipcRenderer.invoke('deskagent:skills:list'),
    setEnabled: (payload: any) => ipcRenderer.invoke('deskagent:skill:set-enabled', payload)
  },
  sprite: {
    getPosition: () => ipcRenderer.invoke('deskagent:sprite:get-position'),
    setAlwaysOnTop: (payload: any) => ipcRenderer.invoke('deskagent:sprite:set-always-on-top', payload),
    setIgnoreMouseEvents: (payload: any) => ipcRenderer.invoke('deskagent:sprite:set-ignore-mouse-events', payload),
    setPosition: (payload: any) => ipcRenderer.invoke('deskagent:sprite:set-position', payload)
  },
  toolsets: {
    list: () => ipcRenderer.invoke('deskagent:toolsets:list'),
    setEnabled: (payload: any) => ipcRenderer.invoke('deskagent:toolset:set-enabled', payload)
  },
  update: {
    check: () => ipcRenderer.invoke('deskagent:update:check'),
    onEvent: (callback: (payload: any) => void) => {
      const listener = (_event: any, payload: any) => callback(payload)
      ipcRenderer.on('deskagent:update-event', listener)

      return () => ipcRenderer.removeListener('deskagent:update-event', listener)
    },
    onRunnerEvent: (callback: (payload: any) => void) => {
      const listener = (_event: any, payload: any) => callback(payload)
      ipcRenderer.on('deskagent:runner-update-event', listener)

      return () => ipcRenderer.removeListener('deskagent:runner-update-event', listener)
    }
  },
  writeClipboard: (text: string) => ipcRenderer.invoke('deskagent:writeClipboard', text)
})
