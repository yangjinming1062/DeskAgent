import assert from 'node:assert/strict'
import test from 'node:test'

import { registerRunnerIpc } from './runner'

function makeFakeIpc() {
  const handlers = new Map<string, (...args: any[]) => any>()

  return {
    handle: (channel: string, handler: (...args: any[]) => any) => handlers.set(channel, handler),
    invoke: (channel: string, ...args: any[]) => {
      const h = handlers.get(channel)

      if (!h) {
        throw new Error(`no handler for ${channel}`)
      }

      return h({}, ...args)
    }
  }
}

function makeDeps(bridge: any): any {
  return { runnerBridge: bridge }
}

test('runner:cancel dispatches spiritagent.cancel to the bridge', async () => {
  const dispatched: any[] = []

  const bridge = {
    dispatch: async (method: string, params: any) => {
      dispatched.push({ method, params })

      return { ok: true }
    }
  }

  const ipc = makeFakeIpc()
  registerRunnerIpc({ deps: makeDeps(bridge), ipcMain: ipc as any })

  const result = await ipc.invoke('spiritagent:runner:cancel')

  assert.deepEqual(result, { ok: true })
  assert.deepEqual(dispatched, [{ method: 'spiritagent.cancel', params: {} }])
})

test('runner:cancel is not rate-bucketed like execute_tool', async () => {
  const dispatched: any[] = []

  const bridge = {
    dispatch: async (method: string, params: any) => {
      dispatched.push({ method, params })

      return { ok: true }
    }
  }

  const ipc = makeFakeIpc()
  registerRunnerIpc({ deps: makeDeps(bridge), ipcMain: ipc as any })

  for (let i = 0; i < 80; i++) {
    assert.deepEqual(await ipc.invoke('spiritagent:runner:cancel'), { ok: true })
  }

  assert.equal(dispatched.length, 80)
})
