// Tray + close-to-tray + single-instance lock — see desktop/CLAUDE.md for
// the contract (`isQuitting` ownership, close-to-tray semantics, dock-hide
// decision). `installCloseInterceptor` must be re-applied by main.cjs for
// every newly created BrowserWindow.

let trayInstance = null
let trayDeps = null

function buildTrayMenu() {
  return trayDeps.Menu.buildFromTemplate([
    { label: 'Show DeskAgent', click: () => showMainWindow() },
    { type: 'separator' },
    { label: 'Quit DeskAgent', click: () => quitAppFully() }
  ])
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
  destroyTray
}
