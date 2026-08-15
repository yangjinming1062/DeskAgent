import type { App, BrowserWindow, Menu, nativeImage, Tray } from 'electron'

export interface TrayDeps {
  Menu: typeof Menu
  Tray: typeof Tray
  app: App
  bridgeDeps: {
    backendSession?: any
    ensureBackendSession?: () => any
    getMainWindow: () => BrowserWindow | null | undefined
    isQuitting?: boolean
    showToolWindow: () => void
  }
  createWindow: () => void
  getAppIconPath: () => null | string
  nativeImage: typeof nativeImage
  rememberLog: (chunk: string) => void
}

let trayInstance: null | Tray = null
let trayDeps: null | TrayDeps = null

// Settings + Activation/Logout live in the tray context menu rather than
// in-app chrome.
function isAuthenticated(): boolean {
  const session = trayDeps?.bridgeDeps?.backendSession || trayDeps?.bridgeDeps?.ensureBackendSession?.()

  return session?.getSession?.()?.hasToken === true
}

function sendToMainWindow(channel: string): void {
  const win = trayDeps?.bridgeDeps?.getMainWindow?.()

  if (win && !win.isDestroyed()) {
    win.webContents.send(channel)
  }
}

function buildTrayMenu() {
  if (!trayDeps) {
    return null
  }

  const authed = isAuthenticated()

  const template: any[] = [
    {
      // When unauthenticated, focus the sprite window (where activation
      // happens) instead of the tool window (which only renders Settings).
      label: authed ? '显示 DeskAgent' : '激活...',
      click: () => showMainWindow()
    }
  ]

  if (authed) {
    template.push(
      { type: 'separator' },
      // The framed tool window self-selects Settings (authed) from $auth.
      { click: () => trayDeps?.bridgeDeps.showToolWindow(), label: '设置...' },
      { click: () => sendToMainWindow('deskagent:tray:logout'), label: '退出登录' }
    )
  }

  template.push({ type: 'separator' }, { click: () => quitAppFully(), label: '退出 DeskAgent' })

  return trayDeps.Menu.buildFromTemplate(template)
}

export function rebuildTrayMenu(): void {
  if (trayInstance && !trayInstance.isDestroyed()) {
    const menu = buildTrayMenu()

    if (menu) {
      trayInstance.setContextMenu(menu)
    }
  }
}

export function installCloseInterceptor(win: BrowserWindow): void {
  win.on('close', event => {
    if (trayDeps?.bridgeDeps.isQuitting) {
      return
    }

    event.preventDefault()
    win.hide()

    if (process.platform === 'win32') {
      win.setSkipTaskbar(true)
    }
  })
}

export function showMainWindow(): void {
  const win = trayDeps?.bridgeDeps.getMainWindow()

  if (!win || win.isDestroyed()) {
    trayDeps?.createWindow()

    return
  }

  if (win.isMinimized()) {
    win.restore()
  }

  if (!win.isVisible()) {
    if (process.platform === 'win32') {
      win.setSkipTaskbar(false)
    }

    win.show()
  }

  win.focus()
}

export function quitAppFully(): void {
  trayDeps?.app.quit()
}

export function installTray(deps: TrayDeps): null | Tray {
  trayDeps = deps

  let image: null | ReturnType<typeof deps.nativeImage.createFromPath> = null

  try {
    const iconPath = trayDeps.getAppIconPath()
    image = iconPath ? deps.nativeImage.createFromPath(iconPath) : null

    if (!image || image.isEmpty()) {
      deps.rememberLog('[tray] no usable icon resolved — operating in hide-only mode')

      return null
    }
  } catch (err: any) {
    deps.rememberLog(`[tray] icon load failed: ${err?.message || err} — operating in hide-only mode`)

    return null
  }

  try {
    trayInstance = new deps.Tray(image)
  } catch (err: any) {
    deps.rememberLog(`[tray] init failed: ${err?.message || err} — operating in hide-only mode`)

    return null
  }

  trayInstance.setToolTip('DeskAgent')
  const menu = buildTrayMenu()

  if (menu) {
    trayInstance.setContextMenu(menu)
  }

  return trayInstance
}

export function destroyTray(): void {
  if (trayInstance && !trayInstance.isDestroyed()) {
    trayInstance.destroy()
  }

  trayInstance = null
}

export function registerSingleInstanceForwarder(deps: TrayDeps): void {
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
