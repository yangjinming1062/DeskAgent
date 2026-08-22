import { type DesktopRunnerState, IPC } from '@ipc/contracts'
import type { BrowserWindow, IpcMain } from 'electron'

import type { BackendSession } from '../backend/session'
import type {
  RunnerBridge,
  RunnerBridgeEvent,
  RunnerBridgeOptions,
  RunnerBridgeStartOptions,
  RunnerBridgeStatus
} from '../runner/bridge'
import type { CreateRunnerProcessOptions, RunnerProcess } from '../runner/process'
import type { ReverseRpcOptions } from '../runner/reverse-rpc'
import type { CreateRunnerWsServerOptions, RunnerWsServer } from '../runner/rpc-ws'
import * as store from '../shared/lib/runner-config-store'

export interface RunnerIpcDeps {
  createReverseRpc: (options: ReverseRpcOptions) => (method: string, params?: unknown) => Promise<unknown>
  createRunnerBridge: (options: RunnerBridgeOptions) => RunnerBridge
  createRunnerProcess: (options: CreateRunnerProcessOptions) => RunnerProcess
  createRunnerWsServer: (options: CreateRunnerWsServerOptions) => RunnerWsServer
  spiritagentHome?: null | string
  ensureBackendSession: () => BackendSession
  fileExists?: (p: string) => boolean
  getMainWindow?: () => BrowserWindow | null | undefined
  rememberLog: (chunk: string) => void
  runnerBridge?: null | RunnerBridge
  taggedLogger: (tag: string) => (msg: string) => void
}

export function ensureRunnerBridge(deps: RunnerIpcDeps): RunnerBridge {
  if (deps.runnerBridge) {
    return deps.runnerBridge
  }

  const pushConfig = () => {
    const bridge = deps.runnerBridge

    if (!bridge) {
      return Promise.resolve()
    }

    return bridge.dispatch('spiritagent.config.update', { config: store.read() })
  }

  deps.runnerBridge = deps.createRunnerBridge({
    spiritagentHome: deps.spiritagentHome,
    log: deps.taggedLogger('[runner-bridge]'),
    processFactory: (args?: RunnerBridgeStartOptions) =>
      deps.createRunnerProcess({
        spiritagentHome: deps.spiritagentHome,
        devPython: process.env.SPIRITAGENT_DESKTOP_PYTHON || null,
        executable: args?.executable || process.env.SPIRITAGENT_DESKTOP_RUNNER_EXECUTABLE || null,
        fileExists: deps.fileExists,
        log: deps.taggedLogger('[runner]'),
        repoRoot: process.env.SPIRITAGENT_DESKTOP_RUNNER_REPO_ROOT || null
      }),
    pushConfig,
    reverseRpcFactory: ({ backendSession, log: rpcLog }: ReverseRpcOptions) =>
      deps.createReverseRpc({
        backendSession,
        log: rpcLog || deps.taggedLogger('[runner-reverse]')
      }),
    wsServerFactory: ({ authToken, log: wsLog, onReverseRpc }: CreateRunnerWsServerOptions) =>
      deps.createRunnerWsServer({
        authToken,
        log: wsLog || deps.taggedLogger('[runner-ws]'),
        onReverseRpc
      })
  })

  store.setPushTarget(pushConfig)

  deps.runnerBridge.onEvent?.((ev: RunnerBridgeEvent) => {
    const win = deps.getMainWindow?.()

    if (win && !win.isDestroyed()) {
      win.webContents.send(IPC.event.runnerStatus, ev)
    }
  })

  return deps.runnerBridge
}

export async function startRunnerBridgeForCurrentSession(
  deps: RunnerIpcDeps
): Promise<{ error?: string; noop?: boolean; ok: boolean; reason?: string; status?: RunnerBridgeStatus }> {
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
  } catch (error: unknown) {
    const msg = error instanceof Error ? error.message : String(error)

    return { error: msg, ok: false }
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
    .catch((error: unknown) => {
      const msg = error instanceof Error ? error.message : String(error)
      deps.rememberLog(`[runner-bridge] auto-start error: ${msg}`)
    })
}

export function autoStopBridge(deps: RunnerIpcDeps): void {
  stopRunnerBridgeForCurrentSession(deps, { reason: 'session-cleared' }).catch((error: unknown) => {
    const msg = error instanceof Error ? error.message : String(error)
    deps.rememberLog(`[runner-bridge] auto-stop failed: ${msg}`)
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

  ipcMain.handle(IPC.invoke.runnerInvoke, async (_event, name: string, args?: Record<string, unknown>) => {
    if (typeof name !== 'string' || !name) {
      throw new Error('runner:invoke requires a non-empty tool name')
    }

    if (!_consumeToken()) {
      throw new Error('runner:invoke rate limit exceeded (token bucket empty)')
    }

    const bridge = ensureRunnerBridge(deps)

    return bridge.invoke(name, args && typeof args === 'object' ? args : {})
  })

  ipcMain.handle(IPC.invoke.runnerGetState, async (): Promise<DesktopRunnerState> => {
    const bridge = deps.runnerBridge

    if (!bridge) {
      return { phase: 'idle' }
    }

    const status = bridge.getStatus()

    return {
      capabilities: status.capabilities ?? null,
      capabilitiesHealth: status.capabilitiesHealth ?? null,
      lastError: status.lastError ?? null,
      phase: status.phase,
      probeFailed: status.probeFailed ?? null,
      runnerVersion: status.runnerVersion ?? null,
      startedAt: status.startedAt ?? null,
      stoppedAt: status.stoppedAt ?? null
    }
  })

  ipcMain.handle(IPC.invoke.runnerReloadMcp, async () => {
    const bridge = ensureRunnerBridge(deps)

    return bridge.dispatch('mcp.reload', {})
  })

  ipcMain.handle(IPC.invoke.runnerCancel, async () => {
    const bridge = ensureRunnerBridge(deps)

    return bridge.dispatch('spiritagent.cancel', {})
  })
}
