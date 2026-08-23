import { IPC, type IpcEventChannel, type IpcEventContract } from '@ipc/contracts'
import type { App, BrowserWindow, Menu, MenuItemConstructorOptions, nativeImage, Tray } from 'electron'

import type { BackendSessionLike } from '../runner/reverse-rpc'

export interface TrayDeps {
  Menu: typeof Menu
  Tray: typeof Tray
  app: App
  bridgeDeps: {
    backendSession?: BackendSessionLike | null
    ensureBackendSession?: () => BackendSessionLike | null | undefined
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

// 设置和激活/反激活放在托盘右键菜单里，而不是应用内界面。
function isAuthenticated(): boolean {
  const session = trayDeps?.bridgeDeps?.backendSession || trayDeps?.bridgeDeps?.ensureBackendSession?.()

  return session?.getSession?.()?.hasToken === true
}

export function isSpriteVisible(): boolean {
  const win = trayDeps?.bridgeDeps?.getMainWindow?.()

  if (!win || win.isDestroyed()) {
    return false
  }

  return win.isVisible() && !win.isMinimized()
}

function sendToMainWindow<C extends IpcEventChannel>(channel: C, ...payload: IpcEventContract[C]): void {
  const win = trayDeps?.bridgeDeps?.getMainWindow?.()

  if (win && !win.isDestroyed()) {
    win.webContents.send(channel, ...payload)
  }
}

export function buildTrayMenu() {
  if (!trayDeps) {
    return null
  }

  const authed = isAuthenticated()
  const visible = isSpriteVisible()

  let mainActionLabel: string
  let mainActionClick: () => void

  if (visible) {
    mainActionLabel = '隐藏'
    mainActionClick = () => hideMainWindow()
  } else if (authed) {
    mainActionLabel = '显示'
    mainActionClick = () => showMainWindow()
  } else {
    mainActionLabel = '激活...'

    mainActionClick = () => {
      showMainWindow()
      // 仅拉窗口不够——激活浮层是 React state，关掉后只能再翻回 true；
      // 通知渲染器把 activationOpen 翻回来。
      sendToMainWindow(IPC.event.trayActivate)
    }
  }

  const template: MenuItemConstructorOptions[] = [
    {
      label: mainActionLabel,
      click: mainActionClick
    }
  ]

  if (authed) {
    template.push({ type: 'separator' }, { click: () => sendToMainWindow(IPC.event.trayLogout), label: '反激活' })
  }

  template.push({ type: 'separator' }, { click: () => quitAppFully(), label: '退出客户端' })

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

    rebuildTrayMenu()
  })

  win.on('show', () => rebuildTrayMenu())
  win.on('hide', () => rebuildTrayMenu())
  win.on('minimize', () => rebuildTrayMenu())
  win.on('restore', () => rebuildTrayMenu())
}

export function hideMainWindow(): void {
  const win = trayDeps?.bridgeDeps?.getMainWindow?.()

  if (win && !win.isDestroyed()) {
    win.hide()

    if (process.platform === 'win32') {
      win.setSkipTaskbar(true)
    }

    rebuildTrayMenu()
  }
}

export function showMainWindow(): void {
  const win = trayDeps?.bridgeDeps?.getMainWindow?.()

  if (!win || win.isDestroyed()) {
    trayDeps?.createWindow()
    rebuildTrayMenu()

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
  rebuildTrayMenu()
}

export function toggleMainWindow(): void {
  if (isSpriteVisible()) {
    hideMainWindow()
  } else {
    showMainWindow()
  }
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
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    deps.rememberLog(`[tray] icon load failed: ${message} — operating in hide-only mode`)

    return null
  }

  try {
    trayInstance = new deps.Tray(image)
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    deps.rememberLog(`[tray] init failed: ${message} — operating in hide-only mode`)

    return null
  }

  trayInstance.setToolTip('SpiritAgent')
  const menu = buildTrayMenu()

  if (menu) {
    trayInstance.setContextMenu(menu)
  }

  trayInstance.on('click', () => {
    toggleMainWindow()
  })

  trayInstance.on('double-click', () => {
    showMainWindow()
  })

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
