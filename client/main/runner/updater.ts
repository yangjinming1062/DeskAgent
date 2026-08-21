import { execFile } from 'node:child_process'
import crypto from 'node:crypto'
import fs from 'node:fs'
import fsp from 'node:fs/promises'
import path from 'node:path'
import { promisify } from 'node:util'

import YAML from 'yaml'

import { sleep } from '../shared/utils'

import { venvPythonFor } from './venv'

const execFileP = promisify(execFile)

export interface RunnerUpdaterDeps {
  bridgeDeps: {
    spiritagentHome: string
    ensureBackendSession?: () => any
    runnerBridge?: any
  }
  fetchImpl?: typeof globalThis.fetch
  getMainWindow: () => any
  log?: (level: string, message: string, ...args: any[]) => void
  sendToMain: (win: any, channel: string, payload: any) => void
}

export class RunnerUpdater {
  bridgeDeps: RunnerUpdaterDeps['bridgeDeps']
  fetchImpl: typeof globalThis.fetch
  getMainWindow: RunnerUpdaterDeps['getMainWindow']
  log?: RunnerUpdaterDeps['log']
  sendToMain: RunnerUpdaterDeps['sendToMain']

  constructor({ bridgeDeps, fetchImpl = globalThis.fetch, getMainWindow, log, sendToMain }: RunnerUpdaterDeps) {
    this.bridgeDeps = bridgeDeps
    this.fetchImpl = fetchImpl
    this.getMainWindow = getMainWindow
    this.sendToMain = sendToMain
    this.log = log
  }

  // 阶段 1：在旧版 Electron 进程内预下载。
  async prefetchRunnerAssets({
    publicKeyPath,
    updateBaseUrl,
    version
  }: {
    publicKeyPath?: null | string
    updateBaseUrl: string
    version: string
  }): Promise<void> {
    const home = this.bridgeDeps.spiritagentHome
    const stagingDir = path.join(home, 'runner.staging')

    await fsp.rm(stagingDir, { force: true, recursive: true })
    await fsp.mkdir(stagingDir, { recursive: true })

    this._emit({ kind: 'runner-prefetching', phase: 'manifest', version })

    const MANIFEST_FETCH_ATTEMPTS = 3
    const MANIFEST_FETCH_BACKOFF_MS = 1500
    let manifest: any
    let primaryErr: any

    for (let attempt = 1; attempt <= MANIFEST_FETCH_ATTEMPTS; attempt++) {
      try {
        const text = await this._fetchText(`${updateBaseUrl}/api/update/latest-runner.yml`)
        manifest = YAML.parse(text)
        primaryErr = null

        break
      } catch (err) {
        primaryErr = err

        if (attempt < MANIFEST_FETCH_ATTEMPTS) {
          await sleep(MANIFEST_FETCH_BACKOFF_MS * attempt)
        }
      }
    }

    if (!manifest) {
      this._emit({
        error: `manifest fetch failed after ${MANIFEST_FETCH_ATTEMPTS} attempts: ${primaryErr?.message || primaryErr}`,
        kind: 'runner-failed',
        phase: 'prefetch',
        version
      })
      throw primaryErr
    }

    if (!manifest.path || !manifest.signature || !manifest.runner) {
      this._emit({
        error: 'manifest missing required fields (path/signature/runner)',
        kind: 'runner-failed',
        phase: 'prefetch',
        version
      })
      throw new Error('manifest missing required fields')
    }

    const manifestSignatureOk = this._verifySignature({
      payload: `${manifest.path}|${manifest.sha512}`,
      publicKeyPath,
      signatureB64: manifest.signature
    })

    if (!manifestSignatureOk) {
      this._emit({
        error: 'manifest signature verification failed',
        kind: 'runner-failed',
        phase: 'prefetch',
        version
      })
      throw new Error('manifest signature verification failed')
    }

    const wheelUrl = `${updateBaseUrl}/api/update/${manifest.path}`
    const wheelStagingPath = path.join(stagingDir, 'wheel.whl')
    const serverPyUrlFinal = `${updateBaseUrl}/api/update/runner/server.py`
    const serverPyStagingPath = path.join(stagingDir, 'server.py')

    this._emit({ kind: 'runner-prefetching', percent: 0, phase: 'wheel', version })
    this._emit({ kind: 'runner-prefetching', percent: 0, phase: 'server', version })
    await Promise.all([
      this._fetchToFile(wheelUrl, wheelStagingPath, manifest.size, pct => {
        this._emit({ kind: 'runner-prefetching', percent: pct, phase: 'wheel', version })
      }),
      this._fetchToFile(serverPyUrlFinal, serverPyStagingPath, null, pct => {
        this._emit({ kind: 'runner-prefetching', percent: pct, phase: 'server', version })
      })
    ])

    const wheelHash = (await hashOfFile(wheelStagingPath, 'sha512')).toUpperCase()

    if (wheelHash !== manifest.sha512) {
      await fsp.rm(stagingDir, { force: true, recursive: true })
      this._emit({
        error: `wheel sha512 mismatch (expected ${manifest.sha512}, got ${wheelHash})`,
        kind: 'runner-failed',
        phase: 'prefetch',
        version
      })
      throw new Error('wheel sha512 mismatch')
    }

    const serverPyHash = await hashOfFile(serverPyStagingPath, 'sha256')

    if (serverPyHash !== manifest.runner.server_py_sha256) {
      await fsp.rm(stagingDir, { force: true, recursive: true })
      this._emit({
        error: 'server.py sha256 mismatch',
        kind: 'runner-failed',
        phase: 'prefetch',
        version
      })
      throw new Error('server.py sha256 mismatch')
    }

    const sentinel = {
      attempt_count: 0,
      max_attempts: 3,
      prepared_at: new Date().toISOString(),
      server_py_path: serverPyStagingPath,
      version,
      wheel_path: wheelStagingPath
    }

    const sentinelPath = path.join(home, '.pending-runner-update.json')
    await fsp.writeFile(sentinelPath, JSON.stringify(sentinel, null, 2), 'utf8')

    this._emit({ kind: 'runner-ready', version })
  }

  // 阶段 2：在新版 Electron 进程内完成安装。
  async installPending(): Promise<{ error?: string; noop?: boolean; ok: boolean }> {
    const home = this.bridgeDeps.spiritagentHome
    const sentinelPath = path.join(home, '.pending-runner-update.json')

    if (!fs.existsSync(sentinelPath)) {
      return { noop: true, ok: true }
    }

    let sentinel: any

    try {
      sentinel = JSON.parse(await fsp.readFile(sentinelPath, 'utf8'))
    } catch (err: any) {
      this._emit({
        error: `sentinel unreadable: ${err?.message || err}`,
        kind: 'runner-failed',
        recoverable: false
      })

      return { error: 'sentinel unreadable', ok: false }
    }

    if (sentinel.attempt_count >= sentinel.max_attempts) {
      await fsp.rm(sentinelPath, { force: true })
      this._emit({
        error: 'max-attempts-exceeded',
        kind: 'runner-failed',
        recoverable: false,
        version: sentinel.version
      })

      return { error: 'max-attempts-exceeded', ok: false }
    }

    const venvPython = venvPythonFor(home)

    let stopResult: any
    let startedNew = false

    const fail = async (reason: string, recoverable: boolean, error?: any) => {
      await this._bumpAttempt(sentinel, sentinelPath, reason)
      this._emit({
        error: error ? `${reason}: ${error?.message || error}` : reason,
        kind: 'runner-failed',
        recoverable,
        version: sentinel.version
      })

      return { error: reason, ok: false }
    }

    const stopIfBridged = () =>
      this.bridgeDeps?.runnerBridge
        ? this.bridgeDeps.runnerBridge.stop({ reason: 'update' })
        : Promise.resolve(undefined)

    try {
      if (!fs.existsSync(venvPython)) {
        await stopIfBridged()

        return await fail('venv-missing', false)
      }

      const [stopRes, venvOk] = await Promise.all([stopIfBridged(), this._probeVenvIntegrity(venvPython)])
      stopResult = stopRes

      if (!venvOk) {
        return await fail(
          'venv-integrity-precheck-failed',
          false,
          new Error(
            'Runner venv imports are broken — the desktop auto-update cannot repair this. ' +
              'Re-run the installer (its `uv venv --clear` rebuilds the venv) and retry.'
          )
        )
      }

      this._emit({ kind: 'runner-installing', percent: 0, phase: 'pip', version: sentinel.version })

      let rollbackMarker: string | null = null

      try {
        const { stdout } = await execFileP(venvPython, ['-m', 'pip', 'show', 'spiritagent-agent'], {
          maxBuffer: 1 * 1024 * 1024,
          timeout: 30_000
        })

        const m = /Name:\s*(\S+)[\s\S]+?Version:\s*(\S+)/.exec(stdout)

        if (m) {
          rollbackMarker = `${m[1]}==${m[2]}`
        }
      } catch (err) {
        this.log?.('debug', '[updater] no pre-existing wheel to snapshot', err)
      }

      try {
        await execFileP(venvPython, ['-m', 'pip', 'install', '--upgrade', sentinel.wheel_path], {
          maxBuffer: 16 * 1024 * 1024,
          timeout: 300_000
        })
      } catch (err) {
        if (rollbackMarker) {
          try {
            await execFileP(venvPython, ['-m', 'pip', 'install', '--upgrade', rollbackMarker], {
              maxBuffer: 16 * 1024 * 1024,
              timeout: 300_000
            })
          } catch (rollbackErr) {
            this.log?.('error', '[updater] pip rollback also failed', rollbackErr)
          }
        }

        return await fail('pip-failed', true, err)
      }

      const serverPyDest = path.join(home, 'runner', 'server.py')
      await fsp.copyFile(sentinel.server_py_path, serverPyDest)

      try {
        await execFileP(
          venvPython,
          ['-c', 'import spiritagent_agent, importlib.util as u; assert u.find_spec("server") is not None'],
          { cwd: path.join(home, 'runner'), timeout: 30_000 }
        )
      } catch (err) {
        if (rollbackMarker) {
          try {
            await execFileP(venvPython, ['-m', 'pip', 'install', '--upgrade', rollbackMarker], {
              maxBuffer: 16 * 1024 * 1024,
              timeout: 300_000
            })
            this.log?.('info', `[updater] rolled back to ${rollbackMarker} after smoke-test failure`)
          } catch (rollbackErr) {
            this.log?.('error', '[updater] rollback after smoke-test failure also failed', rollbackErr)
          }
        }

        return await fail('smoke-test-failed', true, err)
      }

      this._emit({ kind: 'runner-installing', percent: 80, phase: 'starting', version: sentinel.version })

      if (this.bridgeDeps?.runnerBridge) {
        try {
          await this.bridgeDeps.runnerBridge.start({
            backendSession: this.bridgeDeps.ensureBackendSession?.(),
            readyTimeoutMs: 10_000
          })
          startedNew = true
        } catch (err) {
          if (rollbackMarker) {
            try {
              await execFileP(venvPython, ['-m', 'pip', 'install', '--upgrade', rollbackMarker], {
                maxBuffer: 16 * 1024 * 1024,
                timeout: 300_000
              })
              this.log?.('info', `[updater] rolled back to ${rollbackMarker} after start failure`)
            } catch (rollbackErr) {
              this.log?.('error', '[updater] rollback after start failure also failed', rollbackErr)
            }
          }

          return await fail('start-timeout', true, err)
        }
      }

      await fsp.rm(sentinelPath, { force: true })
      await fsp.rm(path.join(home, 'runner.staging'), { force: true, recursive: true })
      this._emit({ kind: 'runner-installed', version: sentinel.version })

      return { ok: true }
    } catch (err) {
      return await fail('unknown', true, err)
    } finally {
      if (stopResult && !startedNew && this.bridgeDeps?.runnerBridge) {
        try {
          await this.bridgeDeps.runnerBridge.start({
            backendSession: this.bridgeDeps.ensureBackendSession?.(),
            readyTimeoutMs: 8_000
          })
          this._emit({ kind: 'runner-recovered', recoverable: true, version: sentinel.version })
        } catch (err: any) {
          this._emit({
            detail: err?.message || String(err),
            error: 'restart-failed-after-update',
            kind: 'runner-failed',
            recoverable: true
          })
          this.log?.('error', '[updater] post-update restart failed', err)
        }
      }
    }
  }

  async _probeVenvIntegrity(venvPython: string): Promise<boolean> {
    try {
      await execFileP(
        venvPython,
        [
          '-c',
          'from typing_extensions import Sentinel; from annotated_types import BaseMetadata; from mcp.types import BaseModel'
        ],
        { timeout: 30_000 }
      )

      return true
    } catch {
      return false
    }
  }

  async _bumpAttempt(sentinel: any, sentinelPath: string, reason: string): Promise<void> {
    sentinel.attempt_count = (sentinel.attempt_count || 0) + 1
    sentinel.last_error = reason

    try {
      await fsp.writeFile(sentinelPath, JSON.stringify(sentinel, null, 2), 'utf8')
    } catch {
      // 尽力而为
    }
  }

  _emit(payload: any): void {
    if (typeof this.sendToMain === 'function') {
      this.sendToMain(this.getMainWindow?.(), 'spiritagent:runner-update-event', payload)
    }
  }

  _verifySignature({
    payload,
    publicKeyPath,
    signatureB64
  }: {
    payload: string
    publicKeyPath?: null | string
    signatureB64: string
  }): boolean {
    if (!publicKeyPath || !fs.existsSync(publicKeyPath)) {
      return false
    }

    try {
      const pubKey = fs.readFileSync(publicKeyPath)
      const verifier = crypto.createVerify('SHA512')
      verifier.update(payload, 'utf8')
      verifier.end()

      return verifier.verify(pubKey, Buffer.from(signatureB64, 'base64'))
    } catch {
      return false
    }
  }

  async _fetchText(url: string): Promise<string> {
    const res = await this.fetchImpl(url, { redirect: 'follow', signal: AbortSignal.timeout(30_000) })

    if (!res.ok) {
      throw new Error(`${res.status} ${res.statusText}`)
    }

    return res.text()
  }

  async _fetchToFile(
    url: string,
    dest: string,
    expectedSize: null | number,
    onProgress?: (pct: number) => void
  ): Promise<void> {
    const res = await this.fetchImpl(url, { redirect: 'follow', signal: AbortSignal.timeout(60_000) })

    if (!res.ok) {
      throw new Error(`${res.status} ${res.statusText}`)
    }

    const total = expectedSize ?? Number(res.headers.get('content-length') ?? 0)
    let received = 0
    const file = fs.createWriteStream(dest)

    try {
      for await (const chunk of (res as any).body) {
        const ok = file.write(chunk)

        if (!ok) {
          await new Promise<void>(r => file.once('drain', () => r()))
        }

        received += chunk.length

        if (total > 0 && typeof onProgress === 'function') {
          onProgress(Math.min(100, Math.round((received / total) * 100)))
        }
      }
    } finally {
      await new Promise<void>((resolve, reject) => {
        file.end((err: any) => (err ? reject(err) : resolve()))
      })
    }
  }
}

async function hashOfFile(p: string, algorithm: string): Promise<string> {
  const h = crypto.createHash(algorithm)
  await new Promise<void>((resolve, reject) => {
    fs.createReadStream(p)
      .on('data', c => h.update(c))
      .on('end', () => resolve())
      .on('error', reject)
  })

  return h.digest('hex')
}
