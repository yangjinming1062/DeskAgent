const { contextBridge, ipcRenderer, webUtils } = require('electron')

contextBridge.exposeInMainWorld('deskagent', {
  getConnection: () => ipcRenderer.invoke('deskagent:connection'),
  getGatewayWsUrl: () => ipcRenderer.invoke('deskagent:gateway:ws-url'),
  getBootProgress: () => ipcRenderer.invoke('deskagent:boot-progress:get'),
  activate: payload => ipcRenderer.invoke('deskagent:auth:activate', payload),
  refreshSession: payload => ipcRenderer.invoke('deskagent:auth:refresh', payload),
  logout: () => ipcRenderer.invoke('deskagent:auth:logout'),
  getSession: () => ipcRenderer.invoke('deskagent:auth:get-session'),
  getDefaultBackendUrl: () => ipcRenderer.invoke('deskagent:auth:get-default-backend-url'),
  // Sprite window → main: bring up the framed tool window (Settings only,
  // post-authentication). The sprite's activation gesture calls this.
  showToolWindow: () => ipcRenderer.invoke('deskagent:window:show-tool'),
  api: request => ipcRenderer.invoke('deskagent:api', request),
  apiAsset: request => ipcRenderer.invoke('deskagent:api:asset', request),
  apiAssetBuffer: request => ipcRenderer.invoke('deskagent:api:asset-buffer', request),
  readFileDataUrl: filePath => ipcRenderer.invoke('deskagent:readFileDataUrl', filePath),
  selectPaths: options => ipcRenderer.invoke('deskagent:selectPaths', options),
  writeClipboard: text => ipcRenderer.invoke('deskagent:writeClipboard', text),
  saveClipboardImage: () => ipcRenderer.invoke('deskagent:saveClipboardImage'),
  log: payload => ipcRenderer.invoke('deskagent:log:emit', payload),
  runnerInvoke: (name, args) => ipcRenderer.invoke('deskagent:runner:invoke', name, args),
  runnerCancel: () => ipcRenderer.invoke('deskagent:runner:cancel'),
  reloadMcp: () => ipcRenderer.invoke('deskagent:runner:reload-mcp'),
  runnerGetTools: () => ipcRenderer.invoke('deskagent:runner:get-tools'),
  // Synchronous snapshot of bridge phase. Pairs with onRunnerStatus so a
  // late subscriber doesn't miss an already-emitted `running` event — see
  // companion/activity.ts startActivityMonitor.
  runnerGetState: () => ipcRenderer.invoke('deskagent:runner:get-state'),
  getPathForFile: file => {
    try {
      return webUtils.getPathForFile(file) || ''
    } catch {
      return ''
    }
  },
  setTitleBarTheme: payload => ipcRenderer.send('deskagent:titlebar-theme', payload),
  runnerConfig: {
    read: () => ipcRenderer.invoke('deskagent:runner-config:read'),
    write: configString => ipcRenderer.invoke('deskagent:runner-config:write', configString),
    patch: patch => ipcRenderer.invoke('deskagent:runner-config:patch', patch)
  },
  skills: {
    list: () => ipcRenderer.invoke('deskagent:skills:list'),
    setEnabled: payload => ipcRenderer.invoke('deskagent:skill:set-enabled', payload)
  },
  toolsets: {
    list: () => ipcRenderer.invoke('deskagent:toolsets:list'),
    setEnabled: payload => ipcRenderer.invoke('deskagent:toolset:set-enabled', payload)
  },
  media: {
    stt: payload => ipcRenderer.invoke('deskagent:media:stt', payload),
    tts: payload => ipcRenderer.invoke('deskagent:media:tts', payload),
    onboardingAudio: {
      read: tag => ipcRenderer.invoke('deskagent:onboardingAudio:read', tag)
    }
  },
  sprite: {
    setIgnoreMouseEvents: payload => ipcRenderer.invoke('deskagent:sprite:set-ignore-mouse-events', payload),
    setAlwaysOnTop: payload => ipcRenderer.invoke('deskagent:sprite:set-always-on-top', payload),
    getPosition: () => ipcRenderer.invoke('deskagent:sprite:get-position'),
    setPosition: payload => ipcRenderer.invoke('deskagent:sprite:set-position', payload)
  },
  onWindowStateChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('deskagent:window-state-changed', listener)
    return () => ipcRenderer.removeListener('deskagent:window-state-changed', listener)
  },
  onPowerResume: callback => {
    const listener = () => callback()
    ipcRenderer.on('deskagent:power-resume', listener)
    return () => ipcRenderer.removeListener('deskagent:power-resume', listener)
  },
  onBootProgress: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('deskagent:boot-progress', listener)
    return () => ipcRenderer.removeListener('deskagent:boot-progress', listener)
  },
  onSessionExpired: callback => {
    const listener = () => callback()
    ipcRenderer.on('deskagent:auth:session-expired', listener)
    return () => ipcRenderer.removeListener('deskagent:auth:session-expired', listener)
  },
  // Auth state is owned per-renderer (two windows = two nanostores). The main
  // process broadcasts every login/logout/refresh to BOTH windows so the
  // sprite (which never runs the login form) learns the new session and can
  // boot/teardown its gateway. Mirrors onSessionExpired's mechanism.
  onAuthChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('deskagent:auth:changed', listener)
    return () => ipcRenderer.removeListener('deskagent:auth:changed', listener)
  },
  onRunnerStatus: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('deskagent:runner:status', listener)
    return () => ipcRenderer.removeListener('deskagent:runner:status', listener)
  },
  onTrayLogout: callback => {
    const listener = () => callback()
    ipcRenderer.on('deskagent:tray:logout', listener)
    return () => ipcRenderer.removeListener('deskagent:tray:logout', listener)
  },
  getVersion: () => ipcRenderer.invoke('deskagent:version'),
  update: {
    check: () => ipcRenderer.invoke('deskagent:update:check'),
    onEvent: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('deskagent:update-event', listener)
      return () => ipcRenderer.removeListener('deskagent:update-event', listener)
    },
    onRunnerEvent: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('deskagent:runner-update-event', listener)
      return () => ipcRenderer.removeListener('deskagent:runner-update-event', listener)
    }
  }
})
