import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildTrayMenu,
  destroyTray,
  installCloseInterceptor,
  installTray,
  isSpriteVisible,
  toggleMainWindow,
  type TrayDeps
} from './tray'

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
  emit: (event: string, ...args: any[]) => void
  webContents: {
    send: (channel: string, ...args: any[]) => void
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
    emit(event: string, ...args: any[]) {
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
  trayInstances: any[]
  lastBuiltMenu: any
} {
  const trayInstances: any[] = []
  let lastBuiltMenu: any = null

  const fakeMenu: any = {
    buildFromTemplate: (template: any[]) => {
      lastBuiltMenu = template

      return { template, items: template }
    }
  }

  class FakeTray {
    image: any
    tooltip = ''
    contextMenu: any = null
    destroyed = false
    listeners: Record<string, Function[]> = {}

    constructor(image: any) {
      this.image = image
      trayInstances.push(this)
    }

    setToolTip(tip: string) {
      this.tooltip = tip
    }

    setContextMenu(menu: any) {
      this.contextMenu = menu
    }

    isDestroyed() {
      return this.destroyed
    }

    destroy() {
      this.destroyed = true
    }

    on(event: string, fn: Function) {
      if (!this.listeners[event]) {
        this.listeners[event] = []
      }

      this.listeners[event].push(fn)
    }

    emit(event: string, ...args: any[]) {
      for (const fn of this.listeners[event] || []) {
        fn(...args)
      }
    }
  }

  const fakeNativeImage: any = {
    createFromPath: (p: string) => ({
      isEmpty: () => false,
      path: p
    })
  }

  const deps: TrayDeps = {
    app: {
      quit: () => {}
    } as any,
    bridgeDeps: {
      backendSession: {
        getSession: () => (options.authed ? { hasToken: true } : { hasToken: false })
      },
      getMainWindow: () => options.fakeWin as any,
      isQuitting: false,
      showToolWindow: () => {}
    },
    createWindow: options.createWindow || (() => {}),
    getAppIconPath: () => '/mock/icon.png',
    Menu: fakeMenu,
    nativeImage: fakeNativeImage,
    rememberLog: () => {},
    Tray: FakeTray as any
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

  const menu = buildTrayMenu() as any
  assert.ok(menu)
  assert.equal(menu.template[0].label, '隐藏')

  // Clicking it should hide the window
  menu.template[0].click()
  assert.equal(fakeWin.hidden, true)

  destroyTray()
})

test('buildTrayMenu shows "激活..." when unauthenticated and sprite is hidden', () => {
  const fakeWin = createFakeWindow({ hidden: true })
  const { deps } = createMockDeps({ authed: false, fakeWin })
  installTray(deps)

  const menu = buildTrayMenu() as any
  assert.ok(menu)
  assert.equal(menu.template[0].label, '激活...')

  // Clicking it should show the window
  menu.template[0].click()
  assert.equal(fakeWin.hidden, false)

  destroyTray()
})

test('buildTrayMenu shows "隐藏" and full menu when authenticated and sprite is visible', () => {
  const fakeWin = createFakeWindow({ hidden: false })
  const { deps } = createMockDeps({ authed: true, fakeWin })
  installTray(deps)

  const menu = buildTrayMenu() as any
  assert.ok(menu)
  assert.equal(menu.template[0].label, '隐藏')

  const labels = menu.template.map((item: any) => item.label).filter(Boolean)
  assert.deepEqual(labels, ['隐藏', '反激活', '退出客户端'])

  destroyTray()
})

test('buildTrayMenu shows "显示" and full menu when authenticated and sprite is hidden', () => {
  const fakeWin = createFakeWindow({ hidden: true })
  const { deps } = createMockDeps({ authed: true, fakeWin })
  installTray(deps)

  const menu = buildTrayMenu() as any
  assert.ok(menu)
  assert.equal(menu.template[0].label, '显示')

  const labels = menu.template.map((item: any) => item.label).filter(Boolean)
  assert.deepEqual(labels, ['显示', '反激活', '退出客户端'])

  destroyTray()
})

test('toggleMainWindow switches between hidden and visible', () => {
  const fakeWin = createFakeWindow({ hidden: false })
  const { deps } = createMockDeps({ authed: true, fakeWin })
  const tray = installTray(deps) as any

  assert.equal(fakeWin.hidden, false)

  toggleMainWindow()
  assert.equal(fakeWin.hidden, true)
  assert.equal(tray.contextMenu.template[0].label, '显示')

  toggleMainWindow()
  assert.equal(fakeWin.hidden, false)
  assert.equal(tray.contextMenu.template[0].label, '隐藏')

  destroyTray()
})

test('installCloseInterceptor rebuilds tray menu on window lifecycle events', () => {
  const fakeWin = createFakeWindow({ hidden: false })
  const { deps } = createMockDeps({ authed: true, fakeWin })
  const tray = installTray(deps) as any
  installCloseInterceptor(fakeWin as any)

  assert.equal(tray.contextMenu.template[0].label, '隐藏')

  // Trigger close event -> intercepted and hidden
  const closeEvent = {
    defaultPrevented: false,
    preventDefault() {
      this.defaultPrevented = true
    }
  }

  fakeWin.emit('close', closeEvent)
  assert.equal(closeEvent.defaultPrevented, true)
  assert.equal(fakeWin.hidden, true)
  assert.equal(tray.contextMenu.template[0].label, '显示')

  // Trigger show event -> menu updates to '隐藏'
  fakeWin.show()
  assert.equal(tray.contextMenu.template[0].label, '隐藏')

  destroyTray()
})

test('installTray handles tray click and double-click events', () => {
  const fakeWin = createFakeWindow({ hidden: false })
  const { deps } = createMockDeps({ authed: true, fakeWin })
  const tray = installTray(deps) as any

  // Tray click toggles
  tray.emit('click')
  assert.equal(fakeWin.hidden, true)

  tray.emit('click')
  assert.equal(fakeWin.hidden, false)

  // Tray double click shows
  fakeWin.hidden = true
  tray.emit('double-click')
  assert.equal(fakeWin.hidden, false)

  destroyTray()
})
