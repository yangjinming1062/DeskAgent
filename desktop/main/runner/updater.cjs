'use strict'

const crypto = require('node:crypto')
const fs = require('node:fs')
const fsp = require('node:fs/promises')
const path = require('node:path')
const { execFile } = require('node:child_process')
const { promisify } = require('node:util')

const execFileP = promisify(execFile)

const { deskagentHome } = require('../security/paths.cjs')
const { venvPythonFor } = require('./venv.cjs')

// Phase 1 / Phase 2 split — see CLAUDE.md §"Electron 二进制自更新".
//
// Phase 1 (prefetchRunnerAssets) runs in the OLD Electron on
// `update-downloaded`. It downloads the runner manifest + wheel + server.py
// to `$DESKAGENT_HOME/runner.staging/`, verifies SHA-512 + RSA signature on each
// asset, and writes a sentinel `.pending-runner-update.json` recording the
// staged paths. The renderer only enables the "Restart now" button AFTER
// phase 1 completes.
//
// Phase 2 (installPending) runs in the NEW Electron at startup. It reads
// the sentinel, kills any running runner via `runnerBridge.stop()` (Windows
// needs the python.exe handle released before we touch the venv), then:
//   - `pip install --upgrade <wheel>` against the existing venv's python
//     (NEVER renames or moves the venv)
//   - overwrites `$DESKAGENT_HOME/runner/server.py`
//   - smoke-imports `deskagent_agent` + `server` to confirm the new wheel
//     loads without ModuleNotFoundError
//   - calls `runnerBridge.start()` to bring the new runner up
//   - on any failure, calls `runnerBridge.start()` again in `finally` to
//     restore the previous runner; increments `attempt_count` in the
//     sentinel; if the count hits `max_attempts`, deletes the sentinel
//     and surfaces a hard `runner-failed {recoverable: false}` event so
//     the renderer can tell the user to reinstall.
class RunnerUpdater {
  constructor({ bridgeDeps, getMainWindow, sendToMain }) {
    this.bridgeDeps = bridgeDeps
    this.getMainWindow = getMainWindow
    this.sendToMain = sendToMain
  }

  // ------------------------------------------------------------------
  // Phase 1: prefetch in the OLD Electron.
  // ------------------------------------------------------------------
  async prefetchRunnerAssets({ version, updateBaseUrl, publicKeyPath }) {
    const home = deskagentHome()
    const stagingDir = path.join(home, 'runner.staging')

    await fsp.rm(stagingDir, { recursive: true, force: true })
    await fsp.mkdir(stagingDir, { recursive: true })

    this._emit({ kind: 'runner-prefetching', version, phase: 'manifest' })

    // 1. Fetch the manifest. `latest-runner.yml` is the only signed source
    //    for the wheel — its top-level signature covers
    //    `<wheel_path>|<wheel_sha512>` and is the single trust anchor.
    //    `latest.yml`'s `runner` block exists but is not independently
    //    signed against the runner's own assets (the block carries a
    //    *secondary* signature in the public manifest schema, not a
    //    first-class one) — using it would require trusting the backend
    //    rewrite layer. We retry the dedicated endpoint with a short
    //    backoff and only fail if it stays down across all attempts;
    //    transient unavailability is the failure mode we want to ride out
    //    here, not unsigned-fallback.
    const MANIFEST_FETCH_ATTEMPTS = 3
    const MANIFEST_FETCH_BACKOFF_MS = 1500
    let manifest
    let primaryErr
    for (let attempt = 1; attempt <= MANIFEST_FETCH_ATTEMPTS; attempt++) {
      try {
        const text = await this._fetchText(`${updateBaseUrl}/api/update/latest-runner.yml`)
        manifest = JSON.parse(text)
        primaryErr = null
        break
      } catch (err) {
        primaryErr = err
        if (attempt < MANIFEST_FETCH_ATTEMPTS) {
          await new Promise(r => setTimeout(r, MANIFEST_FETCH_BACKOFF_MS * attempt))
        }
      }
    }
    if (!manifest) {
      this._emit({
        kind: 'runner-failed',
        version,
        error: `manifest fetch failed after ${MANIFEST_FETCH_ATTEMPTS} attempts: ${primaryErr?.message || primaryErr}`,
        phase: 'prefetch'
      })
      throw primaryErr
    }

    if (!manifest.path || !manifest.signature || !manifest.runner) {
      this._emit({
        kind: 'runner-failed',
        version,
        error: 'manifest missing required fields (path/signature/runner)',
        phase: 'prefetch'
      })
      throw new Error('manifest missing required fields')
    }

    // 2. Verify the manifest's RSA signature against the bundled public key.
    //    Same scheme as electron-updater: payload = "<path>|<sha512>".
    const manifestSignatureOk = this._verifySignature({
      payload: `${manifest.path}|${manifest.sha512}`,
      signatureB64: manifest.signature,
      publicKeyPath
    })
    if (!manifestSignatureOk) {
      this._emit({
        kind: 'runner-failed',
        version,
        error: 'manifest signature verification failed',
        phase: 'prefetch'
      })
      throw new Error('manifest signature verification failed')
    }

    // 3-4. Fetch wheel + server.py in parallel — independent URLs, distinct
    //      staging paths, no shared mutable state. server.py is tiny (~50 KB)
    //      but the round-trip latency savings still matter; the wheel is the
    //      long pole.
    const wheelUrl = `${updateBaseUrl}/api/update/${manifest.path}`
    const wheelStagingPath = path.join(stagingDir, 'wheel.whl')
    const serverPyUrlFinal = `${updateBaseUrl}/api/update/runner/server.py`
    const serverPyStagingPath = path.join(stagingDir, 'server.py')

    this._emit({ kind: 'runner-prefetching', version, phase: 'wheel', percent: 0 })
    this._emit({ kind: 'runner-prefetching', version, phase: 'server', percent: 0 })
    await Promise.all([
      this._fetchToFile(wheelUrl, wheelStagingPath, manifest.size, pct => {
        this._emit({ kind: 'runner-prefetching', version, phase: 'wheel', percent: pct })
      }),
      this._fetchToFile(serverPyUrlFinal, serverPyStagingPath, null, pct => {
        this._emit({ kind: 'runner-prefetching', version, phase: 'server', percent: pct })
      })
    ])

    const wheelHash = (await hashOfFile(wheelStagingPath, 'sha512')).toUpperCase()
    if (wheelHash !== manifest.sha512) {
      await fsp.rm(stagingDir, { recursive: true, force: true })
      this._emit({
        kind: 'runner-failed',
        version,
        error: `wheel sha512 mismatch (expected ${manifest.sha512}, got ${wheelHash})`,
        phase: 'prefetch'
      })
      throw new Error('wheel sha512 mismatch')
    }

    const serverPyHash = await hashOfFile(serverPyStagingPath, 'sha256')
    if (serverPyHash !== manifest.runner.server_py_sha256) {
      await fsp.rm(stagingDir, { recursive: true, force: true })
      this._emit({
        kind: 'runner-failed',
        version,
        error: 'server.py sha256 mismatch',
        phase: 'prefetch'
      })
      throw new Error('server.py sha256 mismatch')
    }

    // 5. Write the sentinel.
    const sentinel = {
      version,
      wheel_path: wheelStagingPath,
      server_py_path: serverPyStagingPath,
      prepared_at: new Date().toISOString(),
      attempt_count: 0,
      max_attempts: 3
    }
    const sentinelPath = path.join(home, '.pending-runner-update.json')
    await fsp.writeFile(sentinelPath, JSON.stringify(sentinel, null, 2), 'utf8')

    this._emit({ kind: 'runner-ready', version })
  }

  // ------------------------------------------------------------------
  // Phase 2: install in the NEW Electron.
  // ------------------------------------------------------------------
  async installPending() {
    const home = deskagentHome()
    const sentinelPath = path.join(home, '.pending-runner-update.json')
    if (!fs.existsSync(sentinelPath)) {
      return { ok: true, noop: true }
    }

    let sentinel
    try {
      sentinel = JSON.parse(await fsp.readFile(sentinelPath, 'utf8'))
    } catch (err) {
      // Corrupt sentinel — refuse to touch anything, surface hard error.
      this._emit({
        kind: 'runner-failed',
        error: `sentinel unreadable: ${err?.message || err}`,
        recoverable: false
      })
      return { ok: false, error: 'sentinel unreadable' }
    }

    if (sentinel.attempt_count >= sentinel.max_attempts) {
      await fsp.rm(sentinelPath, { force: true })
      this._emit({
        kind: 'runner-failed',
        version: sentinel.version,
        error: 'max-attempts-exceeded',
        recoverable: false
      })
      return { ok: false, error: 'max-attempts-exceeded' }
    }

    const venvPython = venvPythonFor(home)

    let stopResult
    let startedNew = false
    const fail = async (reason, recoverable, error) => {
      await this._bumpAttempt(sentinel, sentinelPath, reason)
      this._emit({
        kind: 'runner-failed',
        version: sentinel.version,
        error: error ? `${reason}: ${error?.message || error}` : reason,
        recoverable
      })
      return { ok: false, error: reason }
    }
    const stopIfBridged = () =>
      this.bridgeDeps?.runnerBridge
        ? this.bridgeDeps.runnerBridge.stop({ reason: 'update' })
        : Promise.resolve(undefined)
    try {
      // Hard fast-fail: missing venv python — refuse before spawning.
      if (!fs.existsSync(venvPython)) {
        await stopIfBridged()
        return await fail('venv-missing', false)
      }

      // Stop (releases Windows file handles on the venv) + pre-install
      // integrity probe both run in parallel: the probe only reads imports
      // and is independent of venv state, so the larger of the two
      // latencies is amortized. The probe prevents a 5-minute pip --upgrade
      // from shipping a still-broken runner when the venv has zero-byte .py
      // stubs (interrupted extraction / EDR truncation): pip's
      // only-if-needed strategy skips re-extracting "current" deps, so the
      // corruption survives pip and surfaces on first `runner_ready`.
      // Recovery is `uv venv --clear` in the installer — see
      // `installer/.../bootstrap.rs::runner_venv_is_healthy` (matching
      // import chain so the desktop and installer gates never disagree).
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

      this._emit({ kind: 'runner-installing', version: sentinel.version, phase: 'pip', percent: 0 })

      // 3. Upgrade the wheel in place via the existing venv's pip. NO
      //    --no-deps: pip's default `only-if-needed` strategy pulls only
      //    the diff of transitive deps; --no-deps would forbid pulling
      //    new deps the new wheel introduces and brick the runner.
      //
      //    Snapshot the installed wheel into a rollback marker before pip
      //    so a downstream failure (smoke/start) can revert to the old version.
      let rollbackMarker = null
      try {
        const { stdout } = await execFileP(venvPython, ['-m', 'pip', 'show', 'deskagent-agent'], {
          timeout: 30_000,
          maxBuffer: 1 * 1024 * 1024
        })
        const m = /Name:\s*(\S+)[\s\S]+?Version:\s*(\S+)/.exec(stdout)
        if (m) {
          rollbackMarker = `${m[1]}==${m[2]}`
        }
      } catch (err) {
        // No installed wheel yet — first install. Nothing to roll back to.
        this.log?.('debug', '[updater] no pre-existing wheel to snapshot', err)
      }
      try {
        await execFileP(venvPython, ['-m', 'pip', 'install', '--upgrade', sentinel.wheel_path], {
          timeout: 300_000,
          maxBuffer: 16 * 1024 * 1024
        })
      } catch (err) {
        // pip failed — try to roll back to the prior wheel before bailing.
        if (rollbackMarker) {
          try {
            await execFileP(venvPython, ['-m', 'pip', 'install', '--upgrade', rollbackMarker], {
              timeout: 300_000,
              maxBuffer: 16 * 1024 * 1024
            })
          } catch (rollbackErr) {
            this.log?.('error', '[updater] pip rollback also failed', rollbackErr)
          }
        }
        return await fail('pip-failed', true, err)
      }

      // 4. Overwrite server.py from staging. Skills ship INSIDE the wheel
      //    (as package_data) and are already in place thanks to step 3 —
      //    we don't touch them here.
      const serverPyDest = path.join(home, 'runner', 'server.py')
      await fsp.copyFile(sentinel.server_py_path, serverPyDest)

      // 5. Smoke-test the new wheel + server.py load without errors.
      try {
        await execFileP(
          venvPython,
          ['-c', 'import deskagent_agent, importlib.util as u; assert u.find_spec("server") is not None'],
          { cwd: path.join(home, 'runner'), timeout: 30_000 }
        )
      } catch (err) {
        // Roll back so a failed smoke test doesn't ship a half-baked wheel.
        if (rollbackMarker) {
          try {
            await execFileP(venvPython, ['-m', 'pip', 'install', '--upgrade', rollbackMarker], {
              timeout: 300_000,
              maxBuffer: 16 * 1024 * 1024
            })
            this.log?.('info', `[updater] rolled back to ${rollbackMarker} after smoke-test failure`)
          } catch (rollbackErr) {
            this.log?.('error', '[updater] rollback after smoke-test failure also failed', rollbackErr)
          }
        }
        return await fail('smoke-test-failed', true, err)
      }

      this._emit({ kind: 'runner-installing', version: sentinel.version, phase: 'starting', percent: 80 })

      // 6-7. Start the new runner and wait for it to be ready.
      // `runnerBridge.start()` handles the process spawn, local WS server,
      // and waits for `runner_ready` under the hood.
      if (this.bridgeDeps?.runnerBridge) {
        try {
          await this.bridgeDeps.runnerBridge.start({
            backendSession: this.bridgeDeps.ensureBackendSession?.(),
            readyTimeoutMs: 10_000
          })
          startedNew = true
        } catch (err) {
          // Roll back before the finally path's restart creates a silently broken runner.
          if (rollbackMarker) {
            try {
              await execFileP(venvPython, ['-m', 'pip', 'install', '--upgrade', rollbackMarker], {
                timeout: 300_000,
                maxBuffer: 16 * 1024 * 1024
              })
              this.log?.('info', `[updater] rolled back to ${rollbackMarker} after start failure`)
            } catch (rollbackErr) {
              this.log?.('error', '[updater] rollback after start failure also failed', rollbackErr)
            }
          }
          return await fail('start-timeout', true, err)
        }
      }

      // 8. Success. Clean up.
      await fsp.rm(sentinelPath, { force: true })
      await fsp.rm(path.join(home, 'runner.staging'), { recursive: true, force: true })
      this._emit({ kind: 'runner-installed', version: sentinel.version })
      return { ok: true }
    } catch (err) {
      // Catastrophic — bump attempt, fall through to finally for state restore.
      return await fail('unknown', true, err)
    } finally {
      // Restart the bridge if we stopped the old runner but failed to start
      // a new one; surface a runner-failed event so the UI can offer a retry.
      if (stopResult && !startedNew && this.bridgeDeps?.runnerBridge) {
        try {
          await this.bridgeDeps.runnerBridge.start({
            backendSession: this.bridgeDeps.ensureBackendSession?.(),
            readyTimeoutMs: 8_000
          })
          this._emit({ kind: 'runner-recovered', version: sentinel.version, recoverable: true })
        } catch (err) {
          this._emit({
            kind: 'runner-failed',
            error: 'restart-failed-after-update',
            recoverable: true,
            detail: err?.message || String(err)
          })
          this.log?.('error', '[updater] post-update restart failed', err)
        }
      }
    }
  }

  // ------------------------------------------------------------------
  // helpers
  // ------------------------------------------------------------------

  /// Import chain is intentionally identical to
  /// `installer/.../bootstrap.rs::runner_venv_is_healthy` so the desktop
  /// auto-update gate and the macOS DeskAgent-Setup fast-path gate never
  /// disagree on what "venv is healthy" means.
  async _probeVenvIntegrity(venvPython) {
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

  async _bumpAttempt(sentinel, sentinelPath, reason) {
    sentinel.attempt_count = (sentinel.attempt_count || 0) + 1
    sentinel.last_error = reason
    try {
      await fsp.writeFile(sentinelPath, JSON.stringify(sentinel, null, 2), 'utf8')
    } catch {
      // best effort
    }
  }

  _emit(payload) {
    if (typeof this.sendToMain === 'function') {
      // sendToMain already guards against null/destroyed windows; no try/catch
      // needed here.
      this.sendToMain(this.getMainWindow?.(), 'deskagent:runner-update-event', payload)
    }
  }

  _verifySignature({ payload, signatureB64, publicKeyPath }) {
    if (!publicKeyPath || !fs.existsSync(publicKeyPath)) return false
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

  async _fetchText(url) {
    const res = await fetch(url, { redirect: 'follow', signal: AbortSignal.timeout(30_000) })
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
    return res.text()
  }

  async _fetchToFile(url, dest, expectedSize, onProgress) {
    const res = await fetch(url, { redirect: 'follow', signal: AbortSignal.timeout(60_000) })
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
    const total = expectedSize ?? Number(res.headers.get('content-length') ?? 0)
    let received = 0
    const file = fs.createWriteStream(dest)
    try {
      for await (const chunk of res.body) {
        const ok = file.write(chunk)
        if (!ok) await new Promise(r => file.once('drain', r))
        received += chunk.length
        if (total > 0 && typeof onProgress === 'function') {
          onProgress(Math.min(100, Math.round((received / total) * 100)))
        }
      }
    } finally {
      await new Promise((resolve, reject) => {
        file.end(err => (err ? reject(err) : resolve()))
      })
    }
  }
}

async function hashOfFile(p, algorithm) {
  const h = crypto.createHash(algorithm)
  await new Promise((resolve, reject) => {
    fs.createReadStream(p)
      .on('data', c => h.update(c))
      .on('end', resolve)
      .on('error', reject)
  })
  return h.digest('hex')
}

module.exports = { RunnerUpdater }
