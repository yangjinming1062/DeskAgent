'use strict'

// Runner bridge lifecycle: start/stop/restart/status/invoke on renderer side,
// plus tool-call-forward and get-tools. Bridge stored on shared deps object.

const store = require('../shared/lib/runner-config-store.cjs')

function ensureRunnerBridge(deps) {
  if (deps.runnerBridge) return deps.runnerBridge

  // Shared by bridge (no-arg → reads store) on runner-ready and store (with just-written config) on write.
  const pushConfig = (config = store.read()) => {
    const bridge = deps.runnerBridge
    if (!bridge) return Promise.resolve()
    return bridge.dispatch('deskagent.config.update', { config })
  }

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
    wsServerFactory: ({ onReverseRpc, authToken, log: wsLog }) =>
      deps.createRunnerWsServer({
        onReverseRpc,
        authToken,
        log: wsLog || deps.taggedLogger('[runner-ws]')
      }),
    reverseRpcFactory: ({ backendSession, log: rpcLog }) =>
      deps.createReverseRpc({
        backendSession,
        log: rpcLog || deps.taggedLogger('[runner-reverse]')
      }),
    pushConfig,
    log: deps.taggedLogger('[runner-bridge]')
  })

  store.setPushTarget(pushConfig)

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

  // Synchronous snapshot of the bridge lifecycle. Renderer subscribes to
  // ``deskagent:runner:status`` for future transitions, but Electron's
  // ``ipcRenderer.on`` has no event replay — a renderer that mounts after
  // the bridge already reached ``running`` would never observe it. This
  // getter closes that window (the same pattern auth.cjs uses with
  // ``auth:get-session`` + ``onAuthChanged``). Returns ``{ phase: 'idle' }``
  // when the bridge hasn't been created yet, which is a valid early answer
  // — the subsequent ``running`` event will flip it.
  ipcMain.handle('deskagent:runner:get-state', async () => {
    const bridge = deps.runnerBridge
    if (!bridge) {
      return { phase: 'idle' }
    }
    const status = bridge.getStatus()
    // Project to the fields documented in ``DesktopRunnerState``. The nested
    // ``runner`` / ``wsServer`` sub-statuses are bridge internals (process
    // pid/port, ws connection state) that no renderer consumer reads today;
    // leaking them through IPC would lock the contract to internal shape
    // churn. If a future renderer needs them, add a dedicated channel.
    return {
      phase: status.phase,
      startedAt: status.startedAt ?? null,
      stoppedAt: status.stoppedAt ?? null,
      lastError: status.lastError ?? null,
      capabilities: status.capabilities ?? null,
      runnerVersion: status.runnerVersion ?? null,
      probeFailed: status.probeFailed ?? null
    }
  })

  ipcMain.handle('deskagent:runner:reload-mcp', async () => {
    const bridge = ensureRunnerBridge(deps)
    return bridge.dispatch('mcp.reload', {})
  })

  // Sets the runner's global interrupt flag; in-flight tool handlers observe
  // it on their next is_interrupted() poll and bail out early. Used by the
  // chat Stop button alongside session.interrupt (which stops the LLM stream
  // but not an already-running local command).
  ipcMain.handle('deskagent:runner:cancel', async () => {
    const bridge = ensureRunnerBridge(deps)
    return bridge.dispatch('deskagent.cancel', {})
  })
}

module.exports = {
  registerRunnerIpc,
  ensureRunnerBridge,
  startRunnerBridgeForCurrentSession,
  stopRunnerBridgeForCurrentSession,
  autoStartBridge,
  autoStopBridge
}
