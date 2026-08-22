import assert from 'node:assert/strict'
import test from 'node:test'

import type { App, BrowserWindow, Menu, MenuItemConstructorOptions, NativeImage, nativeImage, Tray } from 'electron'

import {
  buildTrayMenu,
  destroyTray,
  installCloseInterceptor,
  installTray,
  isSpriteVisible,
  toggleMainWindow,
  type TrayDeps
} from './tray'

interface FakeMenuEntry {
  click?: () => void
  label?: string
  type?: string
}

interface FakeBuiltMenu {
  items: FakeMenuEntry[]
  template: FakeMenuEntry[]
}

interface FakeMenu {
  buildFromTemplate: (template: MenuItemConstructorOptions[]) => FakeBuiltMenu
}

interface FakeNativeImage {
  createFromPath: (p: string) => { isEmpty: () => boolean; path: string }
}

interface FakeTray {
  contextMenu: FakeBuiltMenu | null
  destroyed: boolean
  image: NativeImage
  listeners: Record<string, Function[]>
  tooltip: string
  destroy: () => void
  emit: (event: string, ...args: unknown[]) => void
  isDestroyed: () => boolean
  on: (event: string, fn: Function) => void
  setContextMenu: (menu: FakeBuiltMenu | null) => void
  setToolTip: (tip: string) => void
}

interface FakeWindow {
  destroyed: boolean
  focused: boolean
  hidden: boolean
  minimized: boolean
  skipTaskbar: boolean
  listeners: Record<string, Function[]>
  isDestroyed: () => boolean
  isVisible: () => boolean
  isMinimized: () => boolean
  hide: () => void
  show: () => void
  restore: () => void
  focus: () => void
  setSkipTaskbar: (skip: boolean) => void
  on: (event: string, fn: Function) => void
  emit: (event: string, ...args: unknown[]) => void
  webContents: {
    send: (channel: string, ...args: unknown[]) => void
  }
}

function createFakeWindow(initial: Partial<FakeWindow> = {}): FakeWindow {
  const win: FakeWindow = {
    destroyed: initial.destroyed ?? false,
    focused: initial.focused ?? false,
    hidden: initial.hidden ?? false,
    listeners: {},
    minimized: initial.minimized ?? false,
    skipTaskbar: initial.skipTaskbar ?? false,
    isDestroyed() {
      return win.destroyed
    },
    isVisible() {
      return !win.destroyed && !win.hidden
    },
    isMinimized() {
      return !win.destroyed && win.minimized
    },
    hide() {
      win.hidden = true
      win.emit('hide')
    },
    show() {
      win.hidden = false
      win.emit('show')
    },
    restore() {
      win.minimized = false
      win.emit('restore')
    },
    focus() {
      win.focused = true
    },
    setSkipTaskbar(skip: boolean) {
      win.skipTaskbar = skip
    },
    on(event: string, fn: Function) {
      if (!win.listeners[event]) {
        win.listeners[event] = []
      }

      win.listeners[event].push(fn)
    },
    emit(event: string, ...args: unknown[]) {
      for (const fn of win.listeners[event] || []) {
        fn(...args)
      }
    },
    webContents: {
      send: () => {}
    }
  }

  return win
}

function createMockDeps(options: { authed?: boolean; fakeWin?: FakeWindow | null; createWindow?: () => void }): {
  deps: TrayDeps
  trayInstances: FakeTray[]
  lastBuiltMenu: MenuItemConstructorOptions[] | null
} {
  const trayInstances: FakeTray[] = []
  let lastBuiltMenu: MenuItemConstructorOptions[] | null = null

  const fakeMenu: FakeMenu = {
    buildFromTemplate: (template: MenuItemConstructorOptions[]) => {
      lastBuiltMenu = template

      return { template: template as unknown as FakeMenuEntry[], items: template as unknown as FakeMenuEntry[] }
    }
  }

  class FakeTrayImpl {
    contextMenu: FakeBuiltMenu | null = null
    destroyed = false
    image!: NativeImage
    listeners: Record<string, Function[]> = {}

    tooltip = ''

    constructor(image: NativeImage) {
      this.image = image
      trayInstances.push(this)
    }

    destroy() {
      this.destroyed = true
    }

    emit(event: string, ...args: unknown[]) {
      for (const fn of this.listeners[event] || []) {
        fn(...args)
      }
    }

    isDestroyed() {
      return this.destroyed
    }

    on(event: string, fn: Function) {
      if (!this.listeners[event]) {
        this.listeners[event] = []
      }

      this.listeners[event].push(fn)
    }

    setContextMenu(menu: FakeBuiltMenu | null) {
      this.contextMenu = menu
    }

    setToolTip(tip: string) {
      this.tooltip = tip
    }
  }

  const fakeNativeImage: FakeNativeImage = {
    createFromPath: (p: string) => ({
      isEmpty: () => false,
      path: p
    })
  }

  const deps: TrayDeps = {
    app: {
      quit: () => {}
    } as unknown as App,
    bridgeDeps: {
      backendSession: {
        getSession: () => (options.authed ? { hasToken: true } : { hasToken: false })
      },
      getMainWindow: () => options.fakeWin as unknown as BrowserWindow | null | undefined,
      isQuitting: false,
      showToolWindow: () => {}
    },
    createWindow: options.createWindow || (() => {}),
    getAppIconPath: () => '/mock/icon.png',
    Menu: fakeMenu as unknown as typeof Menu,
    nativeImage: fakeNativeImage as unknown as typeof nativeImage,
    rememberLog: () => {},
    Tray: FakeTrayImpl as unknown as typeof Tray
  }

  return {
    deps,
    get lastBuiltMenu() {
      return lastBuiltMenu
    },
    trayInstances
  }
}

test('isSpriteVisible reflects window state accurately', () => {
  const fakeWin = createFakeWindow({ hidden: false, minimized: false })
  const { deps } = createMockDeps({ fakeWin })
  installTray(deps)

  assert.equal(isSpriteVisible(), true)

  fakeWin.hidden = true
  assert.equal(isSpriteVisible(), false)

  fakeWin.hidden = false
  fakeWin.minimized = true
  assert.equal(isSpriteVisible(), false)

  fakeWin.minimized = false
  fakeWin.destroyed = true
  assert.equal(isSpriteVisible(), false)

  destroyTray()
})

test('buildTrayMenu shows "隐藏" when unauthenticated but sprite is visible', () => {
  const fakeWin = createFakeWindow({ hidden: false })
  const { deps } = createMockDeps({ authed: false, fakeWin })
  installTray(deps)

  const menu = buildTrayMenu() as unknown as FakeBuiltMenu
  assert.ok(menu)
  assert.equal(menu.template[0].label, '隐藏')

  // 点击应隐藏窗口
  menu.template[0].click?.()
  assert.equal(fakeWin.hidden, true)

  destroyTray()
})

test('buildTrayMenu shows "激活..." when unauthenticated and sprite is hidden', () => {
  const fakeWin = createFakeWindow({ hidden: true })
  const { deps } = createMockDeps({ authed: false, fakeWin })
  installTray(deps)

  const menu = buildTrayMenu() as unknown as FakeBuiltMenu
  assert.ok(menu)
  assert.equal(menu.template[0].label, '激活...')

  // 点击应显示窗口
  menu.template[0].click?.()
  assert.equal(fakeWin.hidden, false)

  destroyTray()
})

test('buildTrayMenu shows "隐藏" and full menu when authenticated and sprite is visible', () => {
  const fakeWin = createFakeWindow({ hidden: false })
  const { deps } = createMockDeps({ authed: true, fakeWin })
  installTray(deps)

  const menu = buildTrayMenu() as unknown as FakeBuiltMenu
  assert.ok(menu)
  assert.equal(menu.template[0].label, '隐藏')

  const labels = menu.template.map(item => item.label).filter(Boolean)
  assert.deepEqual(labels, ['隐藏', '反激活', '退出客户端'])

  destroyTray()
})

test('buildTrayMenu shows "显示" and full menu when authenticated and sprite is hidden', () => {
  const fakeWin = createFakeWindow({ hidden: true })
  const { deps } = createMockDeps({ authed: true, fakeWin })
  installTray(deps)

  const menu = buildTrayMenu() as unknown as FakeBuiltMenu
  assert.ok(menu)
  assert.equal(menu.template[0].label, '显示')

  const labels = menu.template.map(item => item.label).filter(Boolean)
  assert.deepEqual(labels, ['显示', '反激活', '退出客户端'])

  destroyTray()
})

test('toggleMainWindow switches between hidden and visible', () => {
  const fakeWin = createFakeWindow({ hidden: false })
  const { deps } = createMockDeps({ authed: true, fakeWin })
  const tray = installTray(deps) as unknown as FakeTray

  assert.equal(fakeWin.hidden, false)

  toggleMainWindow()
  assert.equal(fakeWin.hidden, true)
  assert.equal(tray.contextMenu?.template[0].label, '显示')

  toggleMainWindow()
  assert.equal(fakeWin.hidden, false)
  assert.equal(tray.contextMenu?.template[0].label, '隐藏')

  destroyTray()
})

test('installCloseInterceptor rebuilds tray menu on window lifecycle events', () => {
  const fakeWin = createFakeWindow({ hidden: false })
  const { deps } = createMockDeps({ authed: true, fakeWin })
  const tray = installTray(deps) as unknown as FakeTray
  installCloseInterceptor(fakeWin as unknown as BrowserWindow)

  assert.equal(tray.contextMenu?.template[0].label, '隐藏')

  // 触发 close 事件 → 被拦截并隐藏
  const closeEvent = {
    defaultPrevented: false,
    preventDefault() {
      this.defaultPrevented = true
    }
  }

  fakeWin.emit('close', closeEvent)
  assert.equal(closeEvent.defaultPrevented, true)
  assert.equal(fakeWin.hidden, true)
  assert.equal(tray.contextMenu?.template[0].label, '显示')

  // 触发 show 事件 → 菜单更新为 '隐藏'
  fakeWin.show()
  assert.equal(tray.contextMenu?.template[0].label, '隐藏')

  destroyTray()
})

test('installTray handles tray click and double-click events', () => {
  const fakeWin = createFakeWindow({ hidden: false })
  const { deps } = createMockDeps({ authed: true, fakeWin })
  const tray = installTray(deps) as unknown as FakeTray

  // 单击托盘图标切换显示状态
  tray.emit('click')
  assert.equal(fakeWin.hidden, true)

  tray.emit('click')
  assert.equal(fakeWin.hidden, false)

  // 双击托盘图标显示窗口
  fakeWin.hidden = true
  tray.emit('double-click')
  assert.equal(fakeWin.hidden, false)

  destroyTray()
})
