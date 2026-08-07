'use strict'

// Runner bridge lifecycle: start/stop/restart/status/invoke on renderer side,
// plus tool-call-forward and get-tools. Bridge stored on shared deps object.

function ensureRunnerBridge(deps) {
  if (deps.runnerBridge) return deps.runnerBridge
  deps.runnerBridge = deps.createRunnerBridge({
    deskagentHome: deps.deskagentHome,
    processFactory: () =>
      deps.createRunnerProcess({
        executable: process.env.DESKAGENT_DESKTOP_RUNNER_EXECUTABLE || null,
        deskagentHome: deps.deskagentHome,
        devPython: process.env.DESKAGENT_DESKTOP_PYTHON || null,
        repoRoot: process.env.DESKAGENT_DESKTOP_RUNNER_REPO_ROOT || null,
        fileExists: deps.fileExists,
        log: deps.taggedLogger('[runner]')
      }),
    wsServerFactory: ({ onReverseRpc, log: wsLog }) =>
      deps.createRunnerWsServer({
        onReverseRpc,
        log: wsLog || deps.taggedLogger('[runner-ws]')
      }),
    reverseRpcFactory: ({ backendSession, log: rpcLog }) =>
      deps.createReverseRpc({
        backendSession,
        log: rpcLog || deps.taggedLogger('[runner-reverse]')
      }),
    log: deps.taggedLogger('[runner-bridge]')
  })

  deps.runnerBridge.onEvent?.(ev => {
    const win = deps.getMainWindow?.()
    if (win && !win.isDestroyed()) {
      win.webContents.send('deskagent:runner:status', ev)
    }
  })

  return deps.runnerBridge
}

async function startRunnerBridgeForCurrentSession(deps) {
  const session = deps.ensureBackendSession().getSession()
  if (!session?.hasToken) return { ok: false, reason: 'no-session' }

  const bridge = ensureRunnerBridge(deps)
  const status = bridge.getStatus()
  if (status.phase === 'running' || status.phase === 'starting') {
    return { ok: true, noop: true, status }
  }

  try {
    const next = await bridge.start({
      backendSession: deps.ensureBackendSession(),
      readyTimeoutMs: 8_000
    })
    return { ok: true, status: next }
  } catch (error) {
    return { ok: false, error: error?.message || String(error) }
  }
}

async function stopRunnerBridgeForCurrentSession(deps, { reason } = {}) {
  if (!deps.runnerBridge) return { ok: true, noop: true }
  return deps.runnerBridge.stop({ reason: reason || 'desktop-stop' })
}

function autoStartBridge(deps) {
  // Fire-and-forget: failure must not break login.
  startRunnerBridgeForCurrentSession(deps)
    .then(result => {
      if (!result?.ok && !result?.noop) {
        deps.rememberLog(`[runner-bridge] auto-start failed: ${result.error || 'unknown'}`)
      }
    })
    .catch(error => {
      deps.rememberLog(`[runner-bridge] auto-start error: ${error?.message || error}`)
    })
}

function autoStopBridge(deps) {
  stopRunnerBridgeForCurrentSession(deps, { reason: 'session-cleared' }).catch(error => {
    deps.rememberLog(`[runner-bridge] auto-stop failed: ${error?.message || error}`)
  })
}

// Awaited stop+start for callers that need a single resolved result — used by
// the runner-config IPC after writing config.yaml so the renderer can show
// accurate success/failure instead of fire-and-forget.
async function restartRunnerBridge(deps) {
  const session = deps.ensureBackendSession().getSession()
  if (!session?.hasToken) return { ok: false, reason: 'no-session' }

  const stopResult = await stopRunnerBridgeForCurrentSession(deps, { reason: 'config-rewritten' })
  if (!stopResult?.ok && !stopResult?.noop) {
    return { ok: false, error: stopResult?.error || 'stop-failed' }
  }
  return await startRunnerBridgeForCurrentSession(deps)
}

// Token bucket so a misbehaving tool loop can't loop-bomb the runner; the
// inbound IPC side had no guard (reverse-RPC caps the outbound side).
const _invokeBucket = { tokens: 60, lastRefill: Date.now() }
const _INVOKE_RATE = 60 // tokens per second
const _INVOKE_BURST = 60
function _refillBucket() {
  const now = Date.now()
  const elapsed = (now - _invokeBucket.lastRefill) / 1000
  _invokeBucket.tokens = Math.min(_INVOKE_BURST, _invokeBucket.tokens + elapsed * _INVOKE_RATE)
  _invokeBucket.lastRefill = now
}
function _consumeToken() {
  _refillBucket()
  if (_invokeBucket.tokens < 1) {
    return false
  }
  _invokeBucket.tokens -= 1
  return true
}

function registerRunnerIpc({ ipcMain, deps }) {
  // Co-locates every renderer→runner IPC channel. ``deskagent:runner:get-tools``
  // stays in main.cjs because it needs access to ``mainWindow`` to send the
  // tools list back to the renderer; the rest are pure request/response.
  if (!ipcMain) return

  ipcMain.handle('deskagent:runner:invoke', async (_event, name, args) => {
    if (typeof name !== 'string' || !name) {
      throw new Error('runner:invoke requires a non-empty tool name')
    }
    if (!_consumeToken()) {
      throw new Error('runner:invoke rate limit exceeded (token bucket empty)')
    }
    const bridge = ensureRunnerBridge(deps)
    return bridge.invoke(name, args && typeof args === 'object' ? args : {})
  })
}

module.exports = {
  registerRunnerIpc,
  ensureRunnerBridge,
  startRunnerBridgeForCurrentSession,
  stopRunnerBridgeForCurrentSession,
  autoStartBridge,
  autoStopBridge,
  restartRunnerBridge
}
