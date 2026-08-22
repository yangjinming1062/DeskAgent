import assert from 'node:assert/strict'
import test from 'node:test'

import type { IpcMain, IpcMainInvokeEvent } from 'electron'

import type { RunnerIpcDeps } from './runner'
import { registerRunnerIpc } from './runner'

type Handler = (event: IpcMainInvokeEvent, ...args: unknown[]) => Promise<unknown> | unknown

interface FakeIpc {
  handle: (channel: string, handler: Handler) => void
  invoke: (channel: string, ...args: unknown[]) => Promise<unknown>
}

interface DispatchCall {
  method: string
  params: Record<string, unknown> | undefined
}

interface FakeRunnerBridge {
  dispatch: (method: string, params?: Record<string, unknown>) => Promise<{ ok: boolean }>
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

function makeDeps(bridge: FakeRunnerBridge): RunnerIpcDeps {
  return { runnerBridge: bridge as unknown as RunnerIpcDeps['runnerBridge'] } as unknown as RunnerIpcDeps
}

test('runner:cancel dispatches spiritagent.cancel to the bridge', async () => {
  const dispatched: DispatchCall[] = []

  const bridge: FakeRunnerBridge = {
    dispatch: async (method, params) => {
      dispatched.push({ method, params: params ?? {} })

      return { ok: true }
    }
  }

  const ipc = makeFakeIpc()
  registerRunnerIpc({ deps: makeDeps(bridge), ipcMain: ipc as unknown as IpcMain })

  const result = await ipc.invoke('spiritagent:runner:cancel')

  assert.deepEqual(result, { ok: true })
  assert.deepEqual(dispatched, [{ method: 'spiritagent.cancel', params: {} }])
})

test('runner:cancel is not rate-bucketed like execute_tool', async () => {
  const dispatched: DispatchCall[] = []

  const bridge: FakeRunnerBridge = {
    dispatch: async (method, params) => {
      dispatched.push({ method, params: params ?? {} })

      return { ok: true }
    }
  }

  const ipc = makeFakeIpc()
  registerRunnerIpc({ deps: makeDeps(bridge), ipcMain: ipc as unknown as IpcMain })

  for (let i = 0; i < 80; i++) {
    assert.deepEqual(await ipc.invoke('spiritagent:runner:cancel'), { ok: true })
  }

  assert.equal(dispatched.length, 80)
})
