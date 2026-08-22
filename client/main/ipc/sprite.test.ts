import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import type { BrowserWindow, IpcMain, IpcMainInvokeEvent, Screen } from 'electron'

import { POSITION_FILE, readRestPosition, registerSpriteIpc } from './sprite'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

interface FakeDisplay {
  id: number
  workArea: { height: number; width: number; x: number; y: number }
}

const PRIMARY: FakeDisplay = { id: 1, workArea: { height: 1040, width: 1920, x: 0, y: 0 } }
const SECONDARY: FakeDisplay = { id: 2, workArea: { height: 720, width: 1280, x: 1920, y: 0 } }

type Handler = (event: IpcMainInvokeEvent, ...args: unknown[]) => Promise<unknown> | unknown

interface FakeIpc {
  handle: (channel: string, handler: Handler) => void
  invoke: (channel: string, ...args: unknown[]) => Promise<unknown>
}

function makeFakeIpc(): FakeIpc {
  const handlers = new Map<string, Handler>()

  return {
    handle: (channel, handler) => {
      handlers.set(channel, handler)
    },
    invoke: async (channel, ...args) => {
      const h = handlers.get(channel)

      if (!h) {
        throw new Error(`no handler for ${channel}`)
      }

      return h({} as IpcMainInvokeEvent, ...args)
    }
  }
}

function makeFakeWindow(initial: FakeDisplay['workArea']): {
  readonly hidden: boolean
  setBoundsCalls: unknown[]
  win: BrowserWindow
} {
  let bounds = { ...initial }
  const setBoundsCalls: unknown[] = []

  let hidden = false

  const win = {
    hide: () => {
      hidden = true
    },
    isDestroyed: () => false,
    getBounds: () => ({ ...bounds }),
    getContentBounds: () => ({ ...bounds }),
    setBounds: (b: FakeDisplay['workArea']) => {
      setBoundsCalls.push({ ...b })
      bounds = { ...b }
    },
    setAlwaysOnTop: () => {},
    setIgnoreMouseEvents: () => {},
    setSkipTaskbar: () => {}
  } as unknown as BrowserWindow

  return {
    get hidden() {
      return hidden
    },
    setBoundsCalls,
    win
  }
}

function makeFakeScreen(opts: { cursor: { x: number; y: number }; nearest: FakeDisplay }): Screen {
  const displayForRect = (r: { x: number }) => (r.x >= SECONDARY.workArea.x ? SECONDARY : PRIMARY)

  return {
    getCursorScreenPoint: () => ({ ...opts.cursor }),
    getDisplayNearestPoint: () => opts.nearest,
    getDisplayMatching: (rect: { x: number }) => displayForRect(rect)
  } as unknown as Screen
}

function setup(opts: { cursor: { x: number; y: number }; nearest?: FakeDisplay; startOn?: FakeDisplay }) {
  const ipc = makeFakeIpc()
  const fakeWin = makeFakeWindow((opts.startOn ?? PRIMARY).workArea)
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'spiritagent-sprite-test-'))

  registerSpriteIpc({
    deps: {
      getSpriteWindow: () => fakeWin.win,
      getUserDataDir: () => tmpDir,
      screen: makeFakeScreen({ cursor: opts.cursor, nearest: opts.nearest ?? PRIMARY })
    },
    ipcMain: ipc as unknown as IpcMain
  })

  return { fakeWin, ipc, setBoundsCalls: fakeWin.setBoundsCalls, tmpDir }
}

test('sprite:hide hides the sprite window', async () => {
  const { fakeWin, ipc } = setup({ cursor: { x: 100, y: 100 } })

  assert.equal(fakeWin.hidden, false)
  await ipc.invoke('spiritagent:sprite:hide')
  assert.equal(fakeWin.hidden, true)
})

test('move-to-cursor-display snaps the window onto the cursor display and reports origins and cursor', async () => {
  const { ipc, setBoundsCalls } = setup({ cursor: { x: 2000, y: 100 }, nearest: SECONDARY })

  const res = await ipc.invoke('spiritagent:sprite:move-to-cursor-display')

  assert.deepEqual(res, {
    cursor: { x: 2000, y: 100 },
    from: { x: 0, y: 0 },
    to: { x: 1920, y: 0 }
  })
  assert.equal(setBoundsCalls.length, 1)
  assert.deepEqual(setBoundsCalls[0], SECONDARY.workArea)
})

test('move-to-cursor-display is a no-op when the cursor stays on the current display', async () => {
  const { ipc, setBoundsCalls } = setup({ cursor: { x: 100, y: 100 }, nearest: PRIMARY })

  const res = await ipc.invoke('spiritagent:sprite:move-to-cursor-display')

  assert.equal(res, null)
  assert.equal(setBoundsCalls.length, 0)
})

test('set-position persists the rest position together with the window origin', async () => {
  const { ipc, tmpDir } = setup({ cursor: { x: 100, y: 100 }, startOn: SECONDARY })

  await ipc.invoke('spiritagent:sprite:set-position', { x: 300, y: 400 })

  const saved = readRestPosition(tmpDir)
  assert.deepEqual(saved, { origin: { x: 1920, y: 0 }, x: 300, y: 400 })
})

test('readRestPosition tolerates a legacy file without window origin', () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'spiritagent-sprite-test-'))
  fs.writeFileSync(path.join(tmpDir, POSITION_FILE), JSON.stringify({ x: 10, y: 20 }))

  assert.deepEqual(readRestPosition(tmpDir), { x: 10, y: 20 })
  assert.equal(readRestPosition(path.join(__dirname, 'no-such-dir')), null)
})
