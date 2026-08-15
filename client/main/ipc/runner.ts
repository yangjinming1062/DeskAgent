import type { BrowserWindow, IpcMain } from 'electron'

import type { DesktopRunnerState } from '../../renderer/shared/types/global'
import * as store from '../shared/lib/runner-config-store'

export interface RunnerIpcDeps {
  createReverseRpc: (options: any) => any
  createRunnerBridge: (options: any) => any
  createRunnerProcess: (options: any) => any
  createRunnerWsServer: (options: any) => any
  deskagentHome?: null | string
  ensureBackendSession: () => any
  fileExists?: (p: string) => boolean
  getMainWindow?: () => BrowserWindow | null | undefined
  rememberLog: (chunk: string) => void
  runnerBridge?: any
  taggedLogger: (tag: string) => (msg: string) => void
}

export function ensureRunnerBridge(deps: RunnerIpcDeps): any {
  if (deps.runnerBridge) {
    return deps.runnerBridge
  }

  const pushConfig = (config: any = store.read()) => {
    const bridge = deps.runnerBridge

    if (!bridge) {
      return Promise.resolve()
    }

    return bridge.dispatch('deskagent.config.update', { config })
  }

  deps.runnerBridge = deps.createRunnerBridge({
    deskagentHome: deps.deskagentHome,
    log: deps.taggedLogger('[runner-bridge]'),
    processFactory: () =>
      deps.createRunnerProcess({
        deskagentHome: deps.deskagentHome,
        devPython: process.env.DESKAGENT_DESKTOP_PYTHON || null,
        executable: process.env.DESKAGENT_DESKTOP_RUNNER_EXECUTABLE || null,
        fileExists: deps.fileExists,
        log: deps.taggedLogger('[runner]'),
        repoRoot: process.env.DESKAGENT_DESKTOP_RUNNER_REPO_ROOT || null
      }),
    pushConfig,
    reverseRpcFactory: ({ backendSession, log: rpcLog }: any) =>
      deps.createReverseRpc({
        backendSession,
        log: rpcLog || deps.taggedLogger('[runner-reverse]')
      }),
    wsServerFactory: ({ authToken, log: wsLog, onReverseRpc }: any) =>
      deps.createRunnerWsServer({
        authToken,
        log: wsLog || deps.taggedLogger('[runner-ws]'),
        onReverseRpc
      })
  })

  store.setPushTarget(pushConfig)

  deps.runnerBridge.onEvent?.((ev: any) => {
    const win = deps.getMainWindow?.()

    if (win && !win.isDestroyed()) {
      win.webContents.send('deskagent:runner:status', ev)
    }
  })

  return deps.runnerBridge
}

export async function startRunnerBridgeForCurrentSession(
  deps: RunnerIpcDeps
): Promise<{ error?: string; noop?: boolean; ok: boolean; reason?: string; status?: any }> {
  const session = deps.ensureBackendSession().getSession()

  if (!session?.hasToken) {
    return { ok: false, reason: 'no-session' }
  }

  const bridge = ensureRunnerBridge(deps)
  const status = bridge.getStatus()

  if (status.phase === 'running' || status.phase === 'starting') {
    return { noop: true, ok: true, status }
  }

  try {
    const next = await bridge.start({
      backendSession: deps.ensureBackendSession(),
      readyTimeoutMs: 8_000
    })

    return { ok: true, status: next }
  } catch (error: any) {
    return { error: error?.message || String(error), ok: false }
  }
}

export async function stopRunnerBridgeForCurrentSession(
  deps: RunnerIpcDeps,
  { reason }: { reason?: string } = {}
): Promise<{ errors?: string[]; noop?: boolean; ok: boolean }> {
  if (!deps.runnerBridge) {
    return { noop: true, ok: true }
  }

  return deps.runnerBridge.stop({ reason: reason || 'desktop-stop' })
}

export function autoStartBridge(deps: RunnerIpcDeps): void {
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

export function autoStopBridge(deps: RunnerIpcDeps): void {
  stopRunnerBridgeForCurrentSession(deps, { reason: 'session-cleared' }).catch(error => {
    deps.rememberLog(`[runner-bridge] auto-stop failed: ${error?.message || error}`)
  })
}

const _invokeBucket = { lastRefill: Date.now(), tokens: 60 }
const _INVOKE_RATE = 60
const _INVOKE_BURST = 60

function _refillBucket(): void {
  const now = Date.now()
  const elapsed = (now - _invokeBucket.lastRefill) / 1000
  _invokeBucket.tokens = Math.min(_INVOKE_BURST, _invokeBucket.tokens + elapsed * _INVOKE_RATE)
  _invokeBucket.lastRefill = now
}

function _consumeToken(): boolean {
  _refillBucket()

  if (_invokeBucket.tokens < 1) {
    return false
  }

  _invokeBucket.tokens -= 1

  return true
}

export function registerRunnerIpc({ deps, ipcMain }: { deps: RunnerIpcDeps; ipcMain?: IpcMain }): void {
  if (!ipcMain) {
    return
  }

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

  ipcMain.handle('deskagent:runner:get-state', async (): Promise<DesktopRunnerState> => {
    const bridge = deps.runnerBridge

    if (!bridge) {
      return { phase: 'idle' }
    }

    const status = bridge.getStatus()

    return {
      capabilities: status.capabilities ?? null,
      lastError: status.lastError ?? null,
      phase: status.phase,
      probeFailed: status.probeFailed ?? null,
      runnerVersion: status.runnerVersion ?? null,
      startedAt: status.startedAt ?? null,
      stoppedAt: status.stoppedAt ?? null
    }
  })

  ipcMain.handle('deskagent:runner:reload-mcp', async () => {
    const bridge = ensureRunnerBridge(deps)

    return bridge.dispatch('mcp.reload', {})
  })

  ipcMain.handle('deskagent:runner:cancel', async () => {
    const bridge = ensureRunnerBridge(deps)

    return bridge.dispatch('deskagent.cancel', {})
  })
}
