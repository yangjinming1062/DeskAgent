const { contextBridge, ipcRenderer, webUtils } = require('electron')

contextBridge.exposeInMainWorld('deskagent', {
  getConnection: () => ipcRenderer.invoke('deskagent:connection'),
  getGatewayWsUrl: () => ipcRenderer.invoke('deskagent:gateway:ws-url'),
  getBootProgress: () => ipcRenderer.invoke('deskagent:boot-progress:get'),
  login: payload => ipcRenderer.invoke('deskagent:auth:login', payload),
  refreshSession: payload => ipcRenderer.invoke('deskagent:auth:refresh', payload),
  logout: () => ipcRenderer.invoke('deskagent:auth:logout'),
  getSession: () => ipcRenderer.invoke('deskagent:auth:get-session'),
  getDefaultBackendUrl: () => ipcRenderer.invoke('deskagent:auth:get-default-backend-url'),
  // Sprite window → main: bring up the framed tool window (Login when
  // unauthenticated, Settings when authenticated). The sprite's egg-crack
  // gesture calls this to hand the user off to the login form.
  showToolWindow: () => ipcRenderer.invoke('deskagent:window:show-tool'),
  changePassword: payload => ipcRenderer.invoke('deskagent:auth:change-password', payload),
  modelConfig: {
    get: () => ipcRenderer.invoke('deskagent:model-config:get')
  },
  api: request => ipcRenderer.invoke('deskagent:api', request),
  notify: payload => ipcRenderer.invoke('deskagent:notify', payload),
  requestMicrophoneAccess: () => ipcRenderer.invoke('deskagent:requestMicrophoneAccess'),
  readFileDataUrl: filePath => ipcRenderer.invoke('deskagent:readFileDataUrl', filePath),
  readFileText: filePath => ipcRenderer.invoke('deskagent:readFileText', filePath),
  selectPaths: options => ipcRenderer.invoke('deskagent:selectPaths', options),
  writeClipboard: text => ipcRenderer.invoke('deskagent:writeClipboard', text),
  saveImageFromUrl: url => ipcRenderer.invoke('deskagent:saveImageFromUrl', url),
  saveImageBuffer: (data, ext) => ipcRenderer.invoke('deskagent:saveImageBuffer', { data, ext }),
  saveClipboardImage: () => ipcRenderer.invoke('deskagent:saveClipboardImage'),
  runnerInvoke: (name, args) => ipcRenderer.invoke('deskagent:runner:invoke', name, args),
  runnerDispatch: (method, params) => ipcRenderer.invoke('deskagent:runner:dispatch', method, params),
  runnerGetTools: () => ipcRenderer.invoke('deskagent:runner:get-tools'),
  getPathForFile: file => {
    try {
      return webUtils.getPathForFile(file) || ''
    } catch {
      return ''
    }
  },
  normalizePreviewTarget: (target, baseDir) => ipcRenderer.invoke('deskagent:normalizePreviewTarget', target, baseDir),
  watchPreviewFile: url => ipcRenderer.invoke('deskagent:watchPreviewFile', url),
  stopPreviewFileWatch: id => ipcRenderer.invoke('deskagent:stopPreviewFileWatch', id),
  setTitleBarTheme: payload => ipcRenderer.send('deskagent:titlebar-theme', payload),
  setPreviewShortcutActive: active => ipcRenderer.send('deskagent:previewShortcutActive', Boolean(active)),
  openExternal: url => ipcRenderer.invoke('deskagent:openExternal', url),
  fetchLinkTitle: url => ipcRenderer.invoke('deskagent:fetchLinkTitle', url),
  settings: {
    getDefaultProjectDir: () => ipcRenderer.invoke('deskagent:setting:defaultProjectDir:get'),
    setDefaultProjectDir: dir => ipcRenderer.invoke('deskagent:setting:defaultProjectDir:set', dir),
    pickDefaultProjectDir: () => ipcRenderer.invoke('deskagent:setting:defaultProjectDir:pick')
  },
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
  readDir: dirPath => ipcRenderer.invoke('deskagent:fs:readDir', dirPath),
  gitRoot: startPath => ipcRenderer.invoke('deskagent:fs:gitRoot', startPath),
  gitBranch: startPath => ipcRenderer.invoke('deskagent:fs:gitBranch', startPath),
  completePath: params => ipcRenderer.invoke('deskagent:fs:completePath', params),
  media: {
    stt: payload => ipcRenderer.invoke('deskagent:media:stt', payload),
    tts: payload => ipcRenderer.invoke('deskagent:media:tts', payload)
  },
  sprite: {
    setIgnoreMouseEvents: payload => ipcRenderer.invoke('deskagent:sprite:set-ignore-mouse-events', payload),
    setAlwaysOnTop: payload => ipcRenderer.invoke('deskagent:sprite:set-always-on-top', payload),
    getWorkArea: () => ipcRenderer.invoke('deskagent:sprite:get-work-area'),
    setPosition: payload => ipcRenderer.invoke('deskagent:sprite:set-position', payload)
  },
  terminal: {
    dispose: id => ipcRenderer.invoke('deskagent:terminal:dispose', id),
    resize: (id, size) => ipcRenderer.invoke('deskagent:terminal:resize', id, size),
    start: options => ipcRenderer.invoke('deskagent:terminal:start', options),
    write: (id, data) => ipcRenderer.invoke('deskagent:terminal:write', id, data),
    onData: (id, callback) => {
      const channel = `deskagent:terminal:${id}:data`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)
      return () => ipcRenderer.removeListener(channel, listener)
    },
    onExit: (id, callback) => {
      const channel = `deskagent:terminal:${id}:exit`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)
      return () => ipcRenderer.removeListener(channel, listener)
    }
  },
  onClosePreviewRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('deskagent:close-preview-requested', listener)
    return () => ipcRenderer.removeListener('deskagent:close-preview-requested', listener)
  },
  onWindowStateChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('deskagent:window-state-changed', listener)
    return () => ipcRenderer.removeListener('deskagent:window-state-changed', listener)
  },
  onPreviewFileChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('deskagent:preview-file-changed', listener)
    return () => ipcRenderer.removeListener('deskagent:preview-file-changed', listener)
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
  onOpenSettings: callback => {
    const listener = () => callback()
    ipcRenderer.on('deskagent:tray:open-settings', listener)
    return () => ipcRenderer.removeListener('deskagent:tray:open-settings', listener)
  },
  onTrayLogout: callback => {
    const listener = () => callback()
    ipcRenderer.on('deskagent:tray:logout', listener)
    return () => ipcRenderer.removeListener('deskagent:tray:logout', listener)
  },
  getVersion: () => ipcRenderer.invoke('deskagent:version'),
  update: {
    check: () => ipcRenderer.invoke('deskagent:update:check'),
    download: () => ipcRenderer.invoke('deskagent:update:download'),
    install: () => ipcRenderer.invoke('deskagent:update:install'),
    status: () => ipcRenderer.invoke('deskagent:update:status'),
    retryRunnerInstall: () => ipcRenderer.invoke('deskagent:update:runner:install'),
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
