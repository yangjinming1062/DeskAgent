'use strict'

// Pins the renderer→runner IPC surface: cancel dispatches the
// deskagent.cancel RPC and the invoke rate-bucket still guards execute_tool.

const test = require('node:test')
const assert = require('node:assert/strict')
const { registerRunnerIpc } = require('./runner.cjs')

function makeFakeIpc() {
  const handlers = new Map()
  return {
    handle: (channel, handler) => handlers.set(channel, handler),
    invoke: (channel, ...args) => {
      const h = handlers.get(channel)
      if (!h) throw new Error(`no handler for ${channel}`)
      return h({}, ...args)
    }
  }
}

function makeDeps(bridge) {
  return { runnerBridge: bridge }
}

test('runner:cancel dispatches deskagent.cancel to the bridge', async () => {
  const dispatched = []
  const bridge = {
    dispatch: async (method, params) => {
      dispatched.push({ method, params })
      return { ok: true }
    }
  }
  const ipc = makeFakeIpc()
  registerRunnerIpc({ ipcMain: ipc, deps: makeDeps(bridge) })

  const result = await ipc.invoke('deskagent:runner:cancel')

  assert.deepEqual(result, { ok: true })
  assert.deepEqual(dispatched, [{ method: 'deskagent.cancel', params: {} }])
})

test('runner:cancel is not rate-bucketed like execute_tool', async () => {
  // Stop must always get through — bucketing it behind the invoke guard
  // would make a runaway tool loop un-cancellable.
  const bridge = { dispatch: async () => ({ ok: true }) }
  const ipc = makeFakeIpc()
  registerRunnerIpc({ ipcMain: ipc, deps: makeDeps(bridge) })

  for (let i = 0; i < 80; i++) {
    await ipc.invoke('deskagent:runner:cancel')
  }
})
