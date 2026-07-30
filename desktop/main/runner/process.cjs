/**
 * Manages the local Runner child process. After login, Desktop launches
 * the runner's venv Python (at $DESKAGENT_HOME/runner/.venv) or dev Python runner,
 * passing the local WS server URL for bidirectional JSON-RPC communication.
 *
 * Pure-ish: no electron require at the top — call sites inject `childProcess`.
 */

const path = require('node:path')
const { EventEmitter } = require('node:events')
const childProcess = require('node:child_process')
const { resolveVenvPython } = require('./venv.cjs')

const DEFAULT_GRACE_MS = 4_000
const DEFAULT_STOP_TIMEOUT_MS = 8_000
const DEFAULT_HEALTH_TIMEOUT_MS = 8_000

function defaultLogger() {
  return () => {}
}

function resolveRunnerExecutable(options) {
  if (options.executable) return { command: options.executable, args: [], kind: 'binary' }

  const venvResult = resolveVenvPython({
    deskagentHome: options.deskagentHome,
    fileExists: options.fileExists,
    platform: process.platform
  })
  if (venvResult) return venvResult

  if (options.devPython && options.repoRoot && options.fileExists(options.devPython)) {
    return {
      command: options.devPython,
      args: [path.join(options.repoRoot, 'runner', 'server.py')],
      kind: 'dev-python'
    }
  }

  return null
}

function createRunnerProcess(options = {}) {
  const emitter = new EventEmitter()
  const log = options.log || defaultLogger()
  const spawnFn = options.spawn || childProcess.spawn
  const fileExists = options.fileExists || (() => false)
  const stopGraceMs = Number.isFinite(options.stopGraceMs) ? options.stopGraceMs : DEFAULT_GRACE_MS
  const stopTimeoutMs = Number.isFinite(options.stopTimeoutMs) ? options.stopTimeoutMs : DEFAULT_STOP_TIMEOUT_MS

  let state = {
    running: false,
    pid: null,
    exitCode: null,
    exitSignal: null,
    startedAt: null,
    lastError: null,
    command: null,
    args: null,
    kind: null
  }
  let child = null

  function emit(event) {
    emitter.emit('event', event)
  }

  function setState(patch) {
    state = { ...state, ...patch }
  }

  function getStatus() {
    return { ...state }
  }

  function buildArgs({ desktopWs, extraArgs }) {
    const args = ['--desktop-ws', desktopWs]
    if (Array.isArray(extraArgs)) args.push(...extraArgs)
    return args
  }

  async function start(args = {}) {
    if (state.running) {
      throw new Error('Runner is already running.')
    }
    if (!args.desktopWs) {
      throw new Error('start() requires a desktopWs URL.')
    }

    const resolved = resolveRunnerExecutable({
      executable: args.executable,
      deskagentHome: options.deskagentHome,
      fileExists,
      devPython: options.devPython,
      repoRoot: options.repoRoot
    })

    if (!resolved) {
      const err = new Error(
        'Could not resolve a Runner executable. Pass options.executable, install the Runner venv under $DESKAGENT_HOME, or set DESKAGENT_DESKTOP_PYTHON for dev mode.'
      )
      setState({ lastError: err.message })
      throw err
    }

    const argv = [...resolved.args, ...buildArgs(args)]
    const env = {
      ...process.env,
      ...(options.env || {})
    }

    log(`[runner] spawn ${resolved.kind} ${resolved.command} ${argv.join(' ')}`)

    let handle
    try {
      handle = spawnFn(resolved.command, argv, {
        stdio: ['pipe', 'pipe', 'pipe'],
        env,
        windowsHide: true
      })
    } catch (error) {
      const err = error instanceof Error ? error : new Error(String(error))
      setState({ lastError: err.message })
      log(`[runner] spawn failed: ${err.message}`)
      throw err
    }

    child = handle
    setState({
      running: true,
      pid: handle.pid ?? null,
      startedAt: Date.now(),
      exitCode: null,
      exitSignal: null,
      lastError: null,
      command: resolved.command,
      args: argv,
      kind: resolved.kind
    })

    handle.stdout?.on('data', chunk => emit({ type: 'stdout', data: chunk.toString() }))
    handle.stderr?.on('data', chunk => {
      const text = chunk.toString()
      log(`[runner] stderr: ${text.trim()}`)
      emit({ type: 'stderr', data: text })
    })
    handle.on('error', error => {
      const err = error instanceof Error ? error : new Error(String(error))
      log(`[runner] error event: ${err.message}`)
      setState({ lastError: err.message })
      emit({ type: 'error', error: err })
    })
    handle.on('exit', (code, signal) => {
      log(`[runner] exit code=${code} signal=${signal}`)
      setState({ running: false, exitCode: code, exitSignal: signal })
      child = null
      emit({ type: 'exit', code, signal })
    })

    emit({ type: 'start', pid: handle.pid, command: resolved.command, args: argv })
    return getStatus()
  }

  function stop({ reason } = {}) {
    if (!state.running || !child) {
      return Promise.resolve({ ok: true, noop: true })
    }
    log(`[runner] stop reason=${reason || 'unspecified'}`)
    return new Promise(resolve => {
      const target = child
      let settled = false
      // ``fromExit`` distinguishes a real ``'exit'`` event from a timer-driven
      // finalize with stale state. Timed-out finalizes must not report success
      // when the process is still alive.
      const finalize = (code, signal, fromExit) => {
        if (settled) return
        settled = true
        clearTimeout(forceKillTimer)
        target.removeListener('exit', onExit)
        const ok = fromExit ? code === 0 || signal !== null : false
        resolve({ ok, code, signal })
      }
      const onExit = (code, signal) => finalize(code, signal, true)

      target.once('exit', onExit)

      // Read ``target.exitCode`` / ``target.signalCode`` (set by the 'exit'
      // event handler on the ChildProcess itself) and finalize accordingly.
      // Pass ``fromExit=true`` only when the process actually exited —
      // timer-driven finalizes with no real exit must not report success.
      const settleIfExited = fallbackSignal => {
        if (target.exitCode != null || target.signalCode != null) {
          finalize(target.exitCode, target.signalCode, true)
        } else {
          finalize(null, fallbackSignal, false)
        }
      }

      if (process.platform === 'win32') {
        // Async + argv form: doesn't block the event loop, no shell interp.
        childProcess.execFile(
          'taskkill',
          ['/PID', String(target.pid), '/T', '/F'],
          { windowsHide: true, timeout: Math.max(100, stopGraceMs - 100) },
          err => {
            if (err) {
              log(`[runner] taskkill failed for pid=${target.pid}: ${err.message}; falling back to SIGTERM`)
              try {
                target.kill('SIGTERM')
              } catch (error) {
                log(`[runner] SIGTERM fallback failed: ${error.message}`)
              }
            }
          }
        )
      } else {
        try {
          target.kill('SIGTERM')
        } catch (error) {
          log(`[runner] SIGTERM failed: ${error.message}`)
        }
      }

      const forceKillTimer = setTimeout(() => {
        if (settled) return
        log(`[runner] grace expired; force-killing pid=${target.pid}`)
        try {
          target.kill('SIGKILL')
        } catch (error) {
          log(`[runner] SIGKILL failed: ${error.message}`)
        }
        setTimeout(() => {
          if (target.exitCode == null && target.signalCode == null) {
            log(`[runner] pid=${target.pid} still alive after SIGKILL; reporting failure`)
          }
          settleIfExited('SIGKILL')
        }, 500).unref()
      }, stopGraceMs)

      setTimeout(() => settleIfExited(null), stopTimeoutMs).unref()
    })
  }

  async function restart(args) {
    await stop({ reason: 'restart' })
    return start(args)
  }

  function onEvent(callback) {
    emitter.on('event', callback)
    return () => emitter.off('event', callback)
  }

  function waitForReady({ timeoutMs = DEFAULT_HEALTH_TIMEOUT_MS } = {}) {
    return new Promise((resolve, reject) => {
      if (!state.running) {
        reject(new Error('Runner is not running.'))
        return
      }
      const timer = setTimeout(() => {
        detach()
        reject(new Error(`Runner failed to become ready within ${timeoutMs}ms`))
      }, timeoutMs)

      const onEvent = ev => {
        if (ev.type === 'ready') {
          detach()
          clearTimeout(timer)
          resolve(getStatus())
        } else if (ev.type === 'exit') {
          detach()
          clearTimeout(timer)
          reject(new Error(`Runner exited before becoming ready (code=${ev.code}, signal=${ev.signal})`))
        }
      }

      function detach() {
        emitter.off('event', onEvent)
      }

      emitter.on('event', onEvent)
    })
  }

  function signalReady() {
    emit({ type: 'ready' })
  }

  return {
    start,
    stop,
    restart,
    getStatus,
    onEvent,
    waitForReady,
    signalReady
  }
}

module.exports = {
  createRunnerProcess,
  DEFAULT_GRACE_MS,
  DEFAULT_STOP_TIMEOUT_MS,
  DEFAULT_HEALTH_TIMEOUT_MS
}
