const { contextBridge, ipcRenderer, webUtils } = require('electron')

contextBridge.exposeInMainWorld('zastDesktop', {
  getConnection: () => ipcRenderer.invoke('zast:connection'),
  getGatewayWsUrl: () => ipcRenderer.invoke('zast:gateway:ws-url'),
  getBootProgress: () => ipcRenderer.invoke('zast:boot-progress:get'),
  login: payload => ipcRenderer.invoke('zast:auth:login', payload),
  refreshSession: payload => ipcRenderer.invoke('zast:auth:refresh', payload),
  logout: () => ipcRenderer.invoke('zast:auth:logout'),
  getSession: () => ipcRenderer.invoke('zast:auth:get-session'),
  changePassword: payload => ipcRenderer.invoke('zast:auth:change-password', payload),
  modelConfig: {
    get: () => ipcRenderer.invoke('zast:model-config:get')
  },
  api: request => ipcRenderer.invoke('zast:api', request),
  notify: payload => ipcRenderer.invoke('zast:notify', payload),
  requestMicrophoneAccess: () => ipcRenderer.invoke('zast:requestMicrophoneAccess'),
  readFileDataUrl: filePath => ipcRenderer.invoke('zast:readFileDataUrl', filePath),
  readFileText: filePath => ipcRenderer.invoke('zast:readFileText', filePath),
  selectPaths: options => ipcRenderer.invoke('zast:selectPaths', options),
  writeClipboard: text => ipcRenderer.invoke('zast:writeClipboard', text),
  saveImageFromUrl: url => ipcRenderer.invoke('zast:saveImageFromUrl', url),
  saveImageBuffer: (data, ext) => ipcRenderer.invoke('zast:saveImageBuffer', { data, ext }),
  saveClipboardImage: () => ipcRenderer.invoke('zast:saveClipboardImage'),
  runnerInvoke: (name, args) => ipcRenderer.invoke('zast:runner:invoke', name, args),
  runnerDispatch: (method, params) => ipcRenderer.invoke('zast:runner:dispatch', method, params),
  runnerGetTools: () => ipcRenderer.invoke('zast:runner:get-tools'),
  getPathForFile: file => {
    try {
      return webUtils.getPathForFile(file) || ''
    } catch {
      return ''
    }
  },
  normalizePreviewTarget: (target, baseDir) => ipcRenderer.invoke('zast:normalizePreviewTarget', target, baseDir),
  watchPreviewFile: url => ipcRenderer.invoke('zast:watchPreviewFile', url),
  stopPreviewFileWatch: id => ipcRenderer.invoke('zast:stopPreviewFileWatch', id),
  setTitleBarTheme: payload => ipcRenderer.send('zast:titlebar-theme', payload),
  setPreviewShortcutActive: active => ipcRenderer.send('zast:previewShortcutActive', Boolean(active)),
  openExternal: url => ipcRenderer.invoke('zast:openExternal', url),
  fetchLinkTitle: url => ipcRenderer.invoke('zast:fetchLinkTitle', url),
  settings: {
    getDefaultProjectDir: () => ipcRenderer.invoke('zast:setting:defaultProjectDir:get'),
    setDefaultProjectDir: dir => ipcRenderer.invoke('zast:setting:defaultProjectDir:set', dir),
    pickDefaultProjectDir: () => ipcRenderer.invoke('zast:setting:defaultProjectDir:pick')
  },
  runnerConfig: {
    read: () => ipcRenderer.invoke('zast:runner-config:read'),
    write: configString => ipcRenderer.invoke('zast:runner-config:write', configString),
    patch: patch => ipcRenderer.invoke('zast:runner-config:patch', patch)
  },
  skills: {
    list: () => ipcRenderer.invoke('zast:skills:list'),
    setEnabled: payload => ipcRenderer.invoke('zast:skill:set-enabled', payload)
  },
  toolsets: {
    list: () => ipcRenderer.invoke('zast:toolsets:list'),
    setEnabled: payload => ipcRenderer.invoke('zast:toolset:set-enabled', payload)
  },
  readDir: dirPath => ipcRenderer.invoke('zast:fs:readDir', dirPath),
  gitRoot: startPath => ipcRenderer.invoke('zast:fs:gitRoot', startPath),
  gitBranch: startPath => ipcRenderer.invoke('zast:fs:gitBranch', startPath),
  completePath: params => ipcRenderer.invoke('zast:fs:completePath', params),
  media: {
    stt: payload => ipcRenderer.invoke('zast:media:stt', payload),
    tts: payload => ipcRenderer.invoke('zast:media:tts', payload)
  },
  terminal: {
    dispose: id => ipcRenderer.invoke('zast:terminal:dispose', id),
    resize: (id, size) => ipcRenderer.invoke('zast:terminal:resize', id, size),
    start: options => ipcRenderer.invoke('zast:terminal:start', options),
    write: (id, data) => ipcRenderer.invoke('zast:terminal:write', id, data),
    onData: (id, callback) => {
      const channel = `zast:terminal:${id}:data`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)
      return () => ipcRenderer.removeListener(channel, listener)
    },
    onExit: (id, callback) => {
      const channel = `zast:terminal:${id}:exit`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)
      return () => ipcRenderer.removeListener(channel, listener)
    }
  },
  onClosePreviewRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('zast:close-preview-requested', listener)
    return () => ipcRenderer.removeListener('zast:close-preview-requested', listener)
  },
  onWindowStateChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('zast:window-state-changed', listener)
    return () => ipcRenderer.removeListener('zast:window-state-changed', listener)
  },
  onPreviewFileChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('zast:preview-file-changed', listener)
    return () => ipcRenderer.removeListener('zast:preview-file-changed', listener)
  },
  onPowerResume: callback => {
    const listener = () => callback()
    ipcRenderer.on('zast:power-resume', listener)
    return () => ipcRenderer.removeListener('zast:power-resume', listener)
  },
  onBootProgress: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('zast:boot-progress', listener)
    return () => ipcRenderer.removeListener('zast:boot-progress', listener)
  },
  onSessionExpired: callback => {
    const listener = () => callback()
    ipcRenderer.on('zast:auth:session-expired', listener)
    return () => ipcRenderer.removeListener('zast:auth:session-expired', listener)
  },
  onRunnerStatus: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('zast:runner:status', listener)
    return () => ipcRenderer.removeListener('zast:runner:status', listener)
  },
  onOpenSettings: callback => {
    const listener = () => callback()
    ipcRenderer.on('zast:tray:open-settings', listener)
    return () => ipcRenderer.removeListener('zast:tray:open-settings', listener)
  },
  onTrayLogout: callback => {
    const listener = () => callback()
    ipcRenderer.on('zast:tray:logout', listener)
    return () => ipcRenderer.removeListener('zast:tray:logout', listener)
  },
  getVersion: () => ipcRenderer.invoke('zast:version'),
  update: {
    check: () => ipcRenderer.invoke('zast:update:check'),
    download: () => ipcRenderer.invoke('zast:update:download'),
    install: () => ipcRenderer.invoke('zast:update:install'),
    status: () => ipcRenderer.invoke('zast:update:status'),
    retryRunnerInstall: () => ipcRenderer.invoke('zast:update:runner:install'),
    onEvent: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('zast:update-event', listener)
      return () => ipcRenderer.removeListener('zast:update-event', listener)
    },
    onRunnerEvent: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('zast:runner-update-event', listener)
      return () => ipcRenderer.removeListener('zast:runner-update-event', listener)
    }
  }
})
