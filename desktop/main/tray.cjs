// Tray + close-to-tray + single-instance lock — see desktop/CLAUDE.md for
// the contract (`isQuitting` ownership, close-to-tray semantics, dock-hide
// decision). `installCloseInterceptor` must be re-applied by main.cjs for
// every newly created BrowserWindow.

let trayInstance = null
let trayDeps = null

// Settings + Login/Logout live in the tray context menu rather than in-app
// chrome: the companion window is intentionally minimal (sprite + on-demand
// panel), so primary entry points for configuration and account actions are
// the tray. The menu reflects the live auth state — `backendSession` is null
// until the first IPC call hydrates it, and getSession().hasToken flips on
// login/logout. rebuildTrayMenu() re-runs after auth changes so the label set
// (Show/Sign in, Settings, Log out) stays correct.
function isAuthenticated() {
  return trayDeps.bridgeDeps.backendSession?.getSession?.()?.hasToken === true
}

function sendToMainWindow(channel) {
  const win = trayDeps.bridgeDeps.getMainWindow()
  if (win && !win.isDestroyed()) {
    win.webContents.send(channel)
  }
}

function buildTrayMenu() {
  const authed = isAuthenticated()
  const template = [
    {
      label: authed ? 'Show DeskAgent' : 'Sign in...',
      click: () => (authed ? showMainWindow() : trayDeps.bridgeDeps.showToolWindow())
    }
  ]
  if (authed) {
    template.push(
      { type: 'separator' },
      // The framed tool window self-selects Settings (authed) from $auth.
      { label: 'Settings...', click: () => trayDeps.bridgeDeps.showToolWindow() },
      { label: 'Log out', click: () => sendToMainWindow('deskagent:tray:logout') }
    )
  }
  template.push({ type: 'separator' }, { label: 'Quit DeskAgent', click: () => quitAppFully() })
  return trayDeps.Menu.buildFromTemplate(template)
}

function rebuildTrayMenu() {
  if (trayInstance && !trayInstance.isDestroyed()) {
    trayInstance.setContextMenu(buildTrayMenu())
  }
}

function installCloseInterceptor(win) {
  win.on('close', event => {
    if (trayDeps.bridgeDeps.isQuitting) return
    event.preventDefault()
    win.hide()
    if (process.platform === 'win32') {
      win.setSkipTaskbar(true)
    }
  })
}

function showMainWindow() {
  const win = trayDeps.bridgeDeps.getMainWindow()
  if (!win || win.isDestroyed()) {
    trayDeps.createWindow()
    return
  }
  if (win.isMinimized()) win.restore()
  if (!win.isVisible()) {
    if (process.platform === 'win32') win.setSkipTaskbar(false)
    win.show()
  }
  win.focus()
}

function quitAppFully() {
  // Do not flip `isQuitting` here. Cmd+Q on macOS reaches `before-quit`
  // directly without going through this function, so the flag has to be
  // set in one place only — `before-quit` in main.cjs — to cover every
  // exit path uniformly.
  trayDeps.app.quit()
}

function installTray(deps) {
  trayDeps = deps

  if (deps.IS_WSL) {
    deps.rememberLog('[tray] skipped: WSL — Tray API unavailable, hide-only fallback')
    return null
  }

  let image
  try {
    const iconPath = trayDeps.getAppIconPath()
    image = iconPath ? deps.nativeImage.createFromPath(iconPath) : null
    if (!image || image.isEmpty()) {
      deps.rememberLog('[tray] no usable icon resolved — operating in hide-only mode')
      return null
    }
  } catch (err) {
    deps.rememberLog(`[tray] icon load failed: ${err?.message || err} — operating in hide-only mode`)
    return null
  }

  try {
    trayInstance = new deps.Tray(image)
  } catch (err) {
    deps.rememberLog(`[tray] init failed: ${err?.message || err} — operating in hide-only mode`)
    return null
  }

  trayInstance.setToolTip('DeskAgent')
  trayInstance.setContextMenu(buildTrayMenu())

  // On Linux, left-click on the tray icon opens the window. On Windows,
  // `setContextMenu` makes a left-click surface the context menu and
  // suppresses the click event — so the click handler is Linux-only.
  if (process.platform === 'linux') {
    trayInstance.on('click', () => showMainWindow())
  }

  // macOS dock stays visible — the user can click the dock icon to bring
  // the window back (handled by `app.on('activate')` in main.cjs), so we
  // never hide it. This gives three entry points on macOS: dock icon,
  // menu-bar tray menu, and Cmd+Tab — covering the user's expectation
  // that background-running apps remain reachable.

  return trayInstance
}

function destroyTray() {
  if (trayInstance && !trayInstance.isDestroyed()) {
    trayInstance.destroy()
  }
  trayInstance = null
}

function registerSingleInstanceForwarder(deps) {
  trayDeps = deps
  deps.app.on('second-instance', () => {
    deps.rememberLog?.('[instance] second-instance forwarded')
    const win = deps.bridgeDeps.getMainWindow()
    if (!win || win.isDestroyed()) {
      deps.createWindow()
      return
    }
    showMainWindow()
  })
}

module.exports = {
  installTray,
  installCloseInterceptor,
  showMainWindow,
  quitAppFully,
  registerSingleInstanceForwarder,
  destroyTray,
  rebuildTrayMenu
}
