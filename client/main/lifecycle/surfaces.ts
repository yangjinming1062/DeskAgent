// 入口面互斥管理器：生活空间与工作台同一时刻最多一个窗口可见。
//
// 状态写入场景：
//   - 打开入口面：互斥切到指定面（先收起另一面再展示），并将上次打开的面持久化；
//   - 关闭入口面：从进程间通信边界收起当前面，记录上次打开的面但不重写偏好；
//   - 窗口自身关闭：与从进程间通信边界收起保持一致；
//   - 水合历史入口：启动期读取偏好，回灌给调用方，渲染层首屏按此决定双击精灵去向。
//
// 互斥控制：本模块对当前展开面的串行访问用微任务队列排队，避免并发打开产生竞态。
// 消费方：主进程路由分发、托盘菜单、精灵右键与桌面双击入口。

import {
  type DesktopSurfaceChangedEvent,
  type DesktopSurfaceOpenPayload,
  IPC,
  normalizeSurfaceId,
  type SurfaceId
} from '@ipc/contracts'
import { BrowserWindow, type IpcMain } from 'electron'

import * as runnerConfigStore from '../shared/lib/runner-config-store'

export interface SurfacesManager {
  closeSurface: () => Promise<void>
  focusSurface: () => Promise<void>
  getState: () => DesktopSurfaceChangedEvent
  hydrateLastSurface: () => SurfaceId
  onWindowClosed: (id: SurfaceId, win: BrowserWindow) => void
  openSurface: (payload: DesktopSurfaceOpenPayload) => Promise<void>
  registerIpcHandlers: (deps: { ipcMain: IpcMain }) => void
}

interface SurfacesManagerOptions {
  createWindow: (id: SurfaceId, payload?: DesktopSurfaceOpenPayload) => Promise<BrowserWindow>
  navigateWindow?: (win: BrowserWindow, id: SurfaceId, payload: DesktopSurfaceOpenPayload) => Promise<void> | void
  rememberLog?: (chunk: string) => void
}

const LAST_SURFACE_KEY_PATH = ['ui', 'last_surface'] as const

function broadcast(win: BrowserWindow | null, payload: DesktopSurfaceChangedEvent): void {
  if (!win || win.isDestroyed()) {
    return
  }

  win.webContents.send(IPC.event.surfaceChanged, payload)
}

function broadcastAll(payload: DesktopSurfaceChangedEvent): void {
  for (const win of BrowserWindow.getAllWindows()) {
    broadcast(win, payload)
  }
}

async function persistLastSurface(id: SurfaceId): Promise<void> {
  await runnerConfigStore.patch(LAST_SURFACE_KEY_PATH, { value: id })
}

export function createSurfacesManager(options: SurfacesManagerOptions): SurfacesManager {
  const windows = new Map<SurfaceId, BrowserWindow>()
  let openSurfaceId: null | SurfaceId = null
  let pendingChain: Promise<unknown> = Promise.resolve()
  let lastSurface: SurfaceId = 'living'
  let lastSurfaceHydrated = false

  function log(chunk: string): void {
    options.rememberLog?.(chunk)
  }

  function snapshot(): DesktopSurfaceChangedEvent {
    return { lastSurface, open: openSurfaceId }
  }

  function withMutex<T>(task: () => Promise<T>): Promise<T> {
    const next = pendingChain.then(task, task)
    pendingChain = next.catch(() => {})

    return next
  }

  const onWindowClosed = (id: SurfaceId, win: BrowserWindow): void => {
    if (windows.get(id) !== win) {
      return
    }

    windows.delete(id)

    if (openSurfaceId === id) {
      openSurfaceId = null
      broadcastAll(snapshot())
    }
  }

  const openSurface = async (payload: DesktopSurfaceOpenPayload): Promise<void> => {
    const id = normalizeSurfaceId(payload.surface)

    await withMutex(async () => {
      const previous = openSurfaceId

      if (previous && previous !== id) {
        const prevWin = windows.get(previous)

        if (prevWin && !prevWin.isDestroyed()) {
          prevWin.hide()
        }

        openSurfaceId = null
      }

      let win = windows.get(id)

      if (!win || win.isDestroyed()) {
        win = await options.createWindow(id, payload)
        windows.set(id, win)
      } else if (payload.view || payload.sessionId) {
        await options.navigateWindow?.(win, id, payload)
      }

      if (win.isMinimized()) {
        win.restore()
      }

      win.show()
      win.focus()
      openSurfaceId = id
      lastSurface = id
      lastSurfaceHydrated = true

      broadcastAll(snapshot())
      await persistLastSurface(id)
    })
  }

  const closeSurface = async (): Promise<void> => {
    await withMutex(async () => {
      if (!openSurfaceId) {
        return
      }

      const id = openSurfaceId
      const win = windows.get(id)

      if (win && !win.isDestroyed()) {
        win.hide()
      }

      openSurfaceId = null
      broadcastAll(snapshot())
    })
  }

  const focusSurface = async (): Promise<void> => {
    const win = openSurfaceId ? windows.get(openSurfaceId) : null

    if (win && !win.isDestroyed()) {
      if (win.isMinimized()) {
        win.restore()
      }

      win.show()
      win.focus()
    }
  }

  const getState = (): DesktopSurfaceChangedEvent => snapshot()

  const hydrateLastSurface = (): SurfaceId => {
    if (lastSurfaceHydrated) {
      return lastSurface
    }

    const ui = runnerConfigStore.read().ui as { last_surface?: unknown } | undefined
    const id = normalizeSurfaceId(ui?.last_surface)
    lastSurface = id
    lastSurfaceHydrated = true

    return id
  }

  const registerIpcHandlers = ({ ipcMain }: { ipcMain: IpcMain }): void => {
    ipcMain.handle(IPC.invoke.surfaceOpen, (_event, payload: unknown) => {
      const surface = (payload as { surface?: unknown } | null)?.surface
      const view = (payload as { view?: unknown } | null)?.view
      const sessionId = (payload as { sessionId?: unknown } | null)?.sessionId

      return openSurface({
        sessionId: typeof sessionId === 'string' ? sessionId : undefined,
        surface: normalizeSurfaceId(surface),
        view: typeof view === 'string' ? view : undefined
      })
    })

    ipcMain.handle(IPC.invoke.surfaceClose, () => closeSurface())

    ipcMain.handle(IPC.invoke.surfaceFocus, () => focusSurface())

    ipcMain.handle(IPC.invoke.surfaceGetState, () => snapshot())
  }

  log('[surfaces] manager ready')

  return {
    closeSurface,
    focusSurface,
    getState,
    hydrateLastSurface,
    onWindowClosed,
    openSurface,
    registerIpcHandlers
  }
}
