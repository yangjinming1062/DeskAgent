const {
  app,
  BrowserWindow,
  Menu,
  Tray,
  clipboard,
  dialog,
  ipcMain,
  nativeImage,
  nativeTheme,
  net: electronNet,
  powerMonitor,
  protocol,
  safeStorage,
  screen,
  session,
  shell
} = require('electron')
const crypto = require('node:crypto')
const fs = require('node:fs')
const http = require('node:http')
const https = require('node:https')
const path = require('node:path')
const { fileURLToPath, pathToFileURL } = require('node:url')
const { detectRemoteDisplay } = require('./lifecycle/platform.cjs')
const {
  DATA_URL_READ_MAX_BYTES,
  DEFAULT_FETCH_TIMEOUT_MS,
  TEXT_PREVIEW_SOURCE_MAX_BYTES,
  resolvePathTimeoutMs,
  resolveReadableFileForIpc,
  resolveTimeoutMs
} = require('./security/hardening.cjs')
const { registerSystemIpc } = require('./ipc/system.cjs')
const { registerTitlebarIpc } = require('./ipc/titlebar.cjs')
const { registerClipboardIpc } = require('./ipc/clipboard.cjs')
const { registerExternalIpc } = require('./ipc/external.cjs')
const { registerSettingsIpc } = require('./ipc/settings.cjs')
const { registerFilesIpc } = require('./ipc/files.cjs')
const { registerOnboardingAudioIpc } = require('./ipc/onboarding-audio.cjs')
const { registerConnectionIpc } = require('./ipc/connection.cjs')
const { registerMediaIpc, createEnginePrefsCache } = require('./ipc/media.cjs')
const { registerAuthIpc } = require('./ipc/auth.cjs')
const { registerRunnerIpc, autoStartBridge, autoStopBridge, restartRunnerBridge } = require('./ipc/runner.cjs')
const { registerRunnerConfigIpc } = require('./ipc/runner-config.cjs')
const { registerSkillsIpc } = require('./ipc/skills.cjs')
const { registerSpriteIpc } = require('./ipc/sprite.cjs')
const { registerUpdateIpc } = require('./ipc/update.cjs')
const { RunnerUpdater } = require('./runner/updater.cjs')
const { fileExists, directoryExists, sendToMain, atomicWriteFile, sleep } = require('./shared/utils.cjs')
const {
  installTray,
  installCloseInterceptor,
  showMainWindow,
  registerSingleInstanceForwarder,
  destroyTray,
  rebuildTrayMenu
} = require('./lifecycle/tray.cjs')
const { deskagentHome } = require('./security/paths.cjs')
const { STREAMABLE_MEDIA_EXTS, mimeTypeForPath, extensionForMimeType } = require('./shared/mime.cjs')
const log = require('electron-log/main')

const USER_DATA_OVERRIDE = process.env.DESKAGENT_DESKTOP_USER_DATA_DIR
if (USER_DATA_OVERRIDE) {
  const resolvedUserData = path.resolve(USER_DATA_OVERRIDE)
  fs.mkdirSync(resolvedUserData, { recursive: true })
  app.setPath('userData', resolvedUserData)
}

const DEV_SERVER = process.env.DESKAGENT_DESKTOP_DEV_SERVER
const IS_PACKAGED = app.isPackaged
const IS_MAC = process.platform === 'darwin'
const APP_ROOT = app.getAppPath()

// Single-instance lock: must run before `app.whenReady()` (Electron docs).
// When a second process launches, the original instance receives
// `second-instance` and the new one exits with `app.exit(0)` — we don't run
// `app.quit()` because the lock owner is responsible for teardown and
// pretending to quit would race with it. `DESKAGENT_DESKTOP_DISABLE_SINGLE_INSTANCE_LOCK=1`
// opts out for dev workflows that legitimately need two windows.
if (process.env.DESKAGENT_DESKTOP_DISABLE_SINGLE_INSTANCE_LOCK !== '1') {
  if (!app.requestSingleInstanceLock()) {
    app.exit(0)
  }
}

// Remote displays (SSH X11 forwarding, VNC, RDP) make Chromium's GPU
// compositor flicker — accelerated layers can't be presented cleanly over the
// wire, so the window flashes during scroll/streaming/animation. Local
// Windows/macOS composite on the GPU and never see it. Fall back to software
// GPU and never see it. Fall back to software rendering when a remote display
// is detected; it's rock-steady over the wire and the CPU cost is negligible
// next to the connection's latency. Must run before app `ready` — these
// switches only apply pre-launch. Override with DESKAGENT_DESKTOP_DISABLE_GPU
// (1/true → always disable, 0/false → keep GPU on).
const REMOTE_DISPLAY_REASON = detectRemoteDisplay()
if (REMOTE_DISPLAY_REASON) {
  app.disableHardwareAcceleration()
  // Belt-and-suspenders for X11/VNC, where the Viz compositor can still glitch
  // with only --disable-gpu: force compositing onto the CPU too.
  app.commandLine.appendSwitch('disable-gpu-compositing')
  console.log(
    `[deskagent] remote display detected (${REMOTE_DISPLAY_REASON}); disabling GPU hardware acceleration to prevent flicker`
  )
}

// Keep the renderer running at full speed while the window is in the background
// or occluded. The chat transcript streams to screen through a
// requestAnimationFrame-gated flush; Chromium pauses rAF (and clamps timers)
// for backgrounded/occluded renderers, so without these the live answer stalls
// whenever the window loses focus (switching to your editor mid-turn, detached
// devtools, another window covering it) and only paints on refocus or refresh.
// `backgroundThrottling: false` on the BrowserWindow covers the blurred case;
// these process-level switches additionally stop Chromium from backgrounding or
// occlusion-throttling the renderer. Must run before app `ready`.
app.commandLine.appendSwitch('disable-renderer-backgrounding')
app.commandLine.appendSwitch('disable-backgrounding-occluded-windows')
app.commandLine.appendSwitch('disable-background-timer-throttling')

// DESKAGENT_HOME — the user-facing root for everything DeskAgent-related. Mirrors the
// installer module's path conventions (see installer/CLAUDE.md).
//
// DESKAGENT_DESKTOP_USER_DATA_DIR (used by scripts/test-desktop.mjs fresh) puts the sandbox
// DESKAGENT_HOME beneath the throwaway userData dir so a fresh-install run never
// touches the user's real ~/.deskagent / %LOCALAPPDATA%\deskagent. The Windows legacy
// `~/.deskagent` migration (preserve an existing user state when no LOCALAPPDATA
// install yet) is folded into paths.cjs::deskagentHome and activated by passing
// `directoryExists`.
function resolveDeskAgentHome() {
  if (process.env.DESKAGENT_HOME) return path.resolve(process.env.DESKAGENT_HOME)
  if (USER_DATA_OVERRIDE) return path.join(path.resolve(USER_DATA_OVERRIDE), 'deskagent-home')
  return deskagentHome({ directoryExists })
}

const DESKAGENT_HOME = resolveDeskAgentHome()

// active-profile.json records which DeskAgent profile the desktop is configured
// desktop.log lives under DESKAGENT_HOME/logs/ so it sits next to agent.log,
// errors.log, gateway.log produced by deskagent_logging.setup_logging — one log
// directory per user, regardless of which UI surface produced the line.
const DESKTOP_LOG_PATH = path.join(DESKAGENT_HOME, 'logs', 'desktop.log')
const DESKTOP_LOG_FLUSH_MS = 120
const DESKTOP_LOG_BUFFER_MAX_CHARS = 64 * 1024
// Bound desktop.log on disk. It is an append-only forensic log, so a boot loop
// (version-skew crash -> backend exits instantly -> renderer keeps hitting
// Retry) appends the full bootstrap transcript every attempt and grows without
// bound — we have seen it reach ~326 GB and exhaust the disk, which then breaks
// update/install (no room for git/venv/npm temp files).
//
// Mirror the Python logs (deskagent_logging.py RotatingFileHandler, maxBytes x
// backupCount): cascade live -> .1 -> .2 -> .3, drop the oldest. Steady-state
// stays bounded at ~(backupCount + 1) x cap however hard the app loops.
//
// Bounding alone never RECLAIMS an already-huge file: a plain rotation just
// renames the monster to .1 and strands it for a cycle a healthy app may never
// reach. A multi-GB boot-loop transcript has no diagnostic value, so anything
// past the discard ceiling is deleted outright — the updated app self-heals a
// disk a stale build filled, on the next launch.
const DESKTOP_LOG_MAX_BYTES = 10 * 1024 * 1024
const DESKTOP_LOG_BACKUP_COUNT = 3
const DESKTOP_LOG_DISCARD_BYTES = DESKTOP_LOG_MAX_BYTES * 4
const desktopLogBackupPath = n => `${DESKTOP_LOG_PATH}.${n}`
const APP_NAME = 'DeskAgent'
const TITLEBAR_HEIGHT = 34
const MACOS_TRAFFIC_LIGHTS_HEIGHT = 14
const WINDOW_BUTTON_POSITION = {
  x: 24,
  y: TITLEBAR_HEIGHT / 2 - MACOS_TRAFFIC_LIGHTS_HEIGHT / 2
}
// Width Electron reserves for the Windows native min/max/close cluster
// when `titleBarOverlay` is enabled. The OS paints these buttons in the
// top-right corner of the renderer; we have to leave that much room on the
// right edge so our system tools (file browser, haptics, settings) don't sit
// underneath them. macOS uses left-side traffic lights instead and reports a
// position via getWindowButtonPosition(), so this width is non-zero only on
// non-macOS platforms.
const NATIVE_OVERLAY_BUTTON_WIDTH = 144
// Canonical app icon paths, in resolution priority order (first hit wins):
//   1. dev: assets/icon.png — the canonical mark, present at <repo>/client/assets/.
//   2. packaged: extraResources copies assets/icon.ico → <resources>/icon.ico, so
//      `process.resourcesPath/icon.ico` resolves on every install target.
//   3. unpacked-asar fallback: when the asar is split, look inside the unpacked tree.
const APP_ICON_PATHS = [
  path.join(APP_ROOT, 'assets', 'icon.png'),
  path.join(process.resourcesPath, 'icon.ico'),
  path.join(unpackedPathFor(APP_ROOT), 'icon.ico')
]

let rendererTitleBarTheme = null

function getTitleBarOverlayOptions() {
  if (IS_MAC) {
    return { height: TITLEBAR_HEIGHT }
  }

  if (rendererTitleBarTheme) {
    return {
      color: rendererTitleBarTheme.background,
      height: TITLEBAR_HEIGHT,
      symbolColor: rendererTitleBarTheme.foreground
    }
  }

  const useDarkColors = nativeTheme.shouldUseDarkColors

  return {
    color: useDarkColors ? '#111111' : '#f7f7f7',
    height: TITLEBAR_HEIGHT,
    symbolColor: useDarkColors ? '#f7f7f7' : '#242424'
  }
}

const PREVIEW_LANGUAGE_BY_EXT = {
  '.c': 'c',
  '.conf': 'ini',
  '.cpp': 'cpp',
  '.css': 'css',
  '.csv': 'csv',
  '.go': 'go',
  '.graphql': 'graphql',
  '.h': 'c',
  '.hpp': 'cpp',
  '.html': 'html',
  '.java': 'java',
  '.js': 'javascript',
  '.json': 'json',
  '.jsx': 'jsx',
  '.kt': 'kotlin',
  '.lua': 'lua',
  '.md': 'markdown',
  '.mjs': 'javascript',
  '.py': 'python',
  '.rb': 'ruby',
  '.rs': 'rust',
  '.sh': 'shell',
  '.sql': 'sql',
  '.svg': 'xml',
  '.toml': 'toml',
  '.ts': 'typescript',
  '.tsx': 'tsx',
  '.txt': 'text',
  '.xml': 'xml',
  '.yaml': 'yaml',
  '.yml': 'yaml',
  '.zsh': 'shell'
}

app.setName(APP_NAME)
// Windows notification grouping: without this, every Notification from
// Electron is attributed to "electron.app.DeskAgent" instead of "io.deskagent.agent",
// so the Action Center shows split entries per toast and tray pinning shows
// the wrong app name. Matches `package.json#build.appId`.
if (process.platform === 'win32') {
  app.setAppUserModelId('io.deskagent.agent')
}
// Seed the native About panel with the desktop's app version on startup.
// `resolveDeskAgentVersion()` returns `app.getVersion()` — fixed for the lifetime
// of this process; a Tauri DeskAgent-Setup update only takes effect after relaunch.
// The handler at showAboutPanelFresh() re-seeds on each menu-open in case the
// process was reloaded in dev mode.
app.setAboutPanelOptions({
  applicationName: APP_NAME,
  applicationVersion: resolveDeskAgentVersion(),
  copyright: 'Copyright © 2026 DeskAgent'
})

// Custom scheme for streaming local media (video/audio) into the renderer.
// Reading large media through `readFileDataUrl` failed: it base64-loads the
// whole file into memory and is hard-capped at DATA_URL_READ_MAX_BYTES (16 MB),
// so any non-trivial video silently refused to load. Streaming via a protocol
// handler removes the size cap and gives the <video> element seekable,
// range-aware playback. Must be registered before the app is ready.
const MEDIA_PROTOCOL = 'deskagent-media'
// Only audio/video may be streamed. Without this the handler would read any
// non-blocklisted local file (no size cap) for any `fetch(deskagent-media://…)`.
// The membership list itself lives in main/mime.cjs; `STREAMABLE_MEDIA_EXTS`
// is destructured from there.

protocol.registerSchemesAsPrivileged([
  {
    scheme: MEDIA_PROTOCOL,
    privileges: {
      secure: true,
      standard: true,
      stream: true,
      supportFetchAPI: true
    }
  }
])

function registerMediaProtocol() {
  protocol.handle(MEDIA_PROTOCOL, async request => {
    let resolvedPath
    try {
      const url = new URL(request.url)
      const filePath = decodeURIComponent(url.pathname.replace(/^\/+/, ''))
      ;({ resolvedPath } = await resolveReadableFileForIpc(filePath, { purpose: 'Media stream' }))
    } catch {
      return new Response('Media not found', { status: 404 })
    }

    if (!STREAMABLE_MEDIA_EXTS.has(path.extname(resolvedPath).toLowerCase())) {
      return new Response('Unsupported media type', { status: 415 })
    }

    // Delegate to Electron's net stack on a file:// URL — it resolves the
    // content-type and honors Range requests so seeking works. Forward the
    // renderer's headers (notably Range) and skip custom-protocol re-entry.
    return electronNet.fetch(pathToFileURL(resolvedPath).toString(), {
      bypassCustomProtocolHandlers: true,
      headers: request.headers
    })
  })
}

let mainWindow = null
// The companion sprite window is the resident `mainWindow` (transparent,
// always-on-top). The framed tool window (Login / Settings) is created on
// demand into `toolWindow`.
let toolWindow = null
let spriteBoundsListenerInstalled = false
// Auto-reload budget for renderer crashes. A deterministic startup crash would
// otherwise loop forever (reload → crash → reload), pinning CPU and spamming
// logs. Allow a few reloads per rolling window, then stop and leave the dead
// window so the user can read the error / quit.
const RENDERER_RELOAD_WINDOW_MS = 60_000
const RENDERER_RELOAD_MAX = 3
let rendererReloadTimes = []
const deskagentLog = []
let previewShortcutActive = false
let desktopLogBuffer = ''
let desktopLogFlushTimer = null
let desktopLogFlushPromise = Promise.resolve()
let nativeThemeListenerInstalled = false
let bootProgressState = {
  error: null,
  message: 'Waiting to start DeskAgent backend',
  phase: 'idle',
  progress: 0,
  running: false,
  timestamp: Date.now()
}

// Pure planner: ordered fs ops to bound a live log of `size`. [] = nothing.
// Each step is ['rm', path] or ['mv', src, dst]; executed best-effort so a
// missing chain link never aborts the rest.
function planDesktopLogRotation(size) {
  if (size < DESKTOP_LOG_MAX_BYTES) return []
  const backups = n => Array.from({ length: n }, (_, i) => desktopLogBackupPath(i + 1))
  // Pathological boot-loop log: reclaim live + every backup outright.
  if (size > DESKTOP_LOG_DISCARD_BYTES) {
    return [DESKTOP_LOG_PATH, ...backups(DESKTOP_LOG_BACKUP_COUNT)].map(p => ['rm', p])
  }
  // Cascade: drop oldest, shift each up, live -> .1.
  const ops = [['rm', desktopLogBackupPath(DESKTOP_LOG_BACKUP_COUNT)]]
  for (let i = DESKTOP_LOG_BACKUP_COUNT - 1; i >= 1; i--) {
    ops.push(['mv', desktopLogBackupPath(i), desktopLogBackupPath(i + 1)])
  }
  ops.push(['mv', DESKTOP_LOG_PATH, desktopLogBackupPath(1)])
  return ops
}

function rotateDesktopLogIfNeededSync() {
  let size
  try {
    size = fs.statSync(DESKTOP_LOG_PATH).size
  } catch {
    return // No live file yet — the append (re)creates it.
  }
  for (const [op, src, dst] of planDesktopLogRotation(size)) {
    try {
      if (op === 'rm') fs.rmSync(src, { force: true })
      else fs.renameSync(src, dst)
    } catch {
      // Best-effort — logging must never block startup/shutdown.
    }
  }
}

async function rotateDesktopLogIfNeededAsync() {
  let size
  try {
    size = (await fs.promises.stat(DESKTOP_LOG_PATH)).size
  } catch {
    return // No live file yet — the append (re)creates it.
  }
  for (const [op, src, dst] of planDesktopLogRotation(size)) {
    try {
      if (op === 'rm') await fs.promises.rm(src, { force: true })
      else await fs.promises.rename(src, dst)
    } catch {
      // Best-effort — logging must never crash the shell.
    }
  }
}

function flushDesktopLogBufferSync() {
  if (!desktopLogBuffer) return
  const chunk = desktopLogBuffer
  desktopLogBuffer = ''

  try {
    fs.mkdirSync(path.dirname(DESKTOP_LOG_PATH), { recursive: true })
    rotateDesktopLogIfNeededSync()
    fs.appendFileSync(DESKTOP_LOG_PATH, chunk)
  } catch {
    // Logging must never block app startup/shutdown.
  }
}

function flushDesktopLogBufferAsync() {
  if (!desktopLogBuffer) return desktopLogFlushPromise
  const chunk = desktopLogBuffer
  desktopLogBuffer = ''

  desktopLogFlushPromise = desktopLogFlushPromise
    .then(async () => {
      await fs.promises.mkdir(path.dirname(DESKTOP_LOG_PATH), { recursive: true })
      await rotateDesktopLogIfNeededAsync()
      await fs.promises.appendFile(DESKTOP_LOG_PATH, chunk)
    })
    .catch(() => {
      // Logging must never crash the desktop shell.
    })

  return desktopLogFlushPromise
}

function scheduleDesktopLogFlush() {
  if (desktopLogFlushTimer) return
  desktopLogFlushTimer = setTimeout(() => {
    desktopLogFlushTimer = null
    void flushDesktopLogBufferAsync()
  }, DESKTOP_LOG_FLUSH_MS)
}

function rememberLog(chunk) {
  const text = String(chunk || '').trim()
  if (!text) return

  // Mirror to stdout when running from source (`pnpm dev`). The packaged
  // build has no terminal attached, so this stays silent there. Lets the
  // dev operator see Runner handshake, `tools.sync` payloads, IPC failures
  // etc. live without tailing the desktop log file.
  if (!IS_PACKAGED) {
    const colored = process.stdout.isTTY
    if (colored) {
      process.stdout.write(`\x1b[2m[deskagent]\x1b[0m ${text}\n`)
    } else {
      process.stdout.write(`[deskagent] ${text}\n`)
    }
  }

  const lines = text.split(/\r?\n/).map(line => `[deskagent] ${line}`)
  deskagentLog.push(...lines)
  if (deskagentLog.length > 300) {
    deskagentLog.splice(0, deskagentLog.length - 300)
  }

  desktopLogBuffer += `${lines.join('\n')}\n`

  if (desktopLogBuffer.length >= DESKTOP_LOG_BUFFER_MAX_CHARS) {
    if (desktopLogFlushTimer) {
      clearTimeout(desktopLogFlushTimer)
      desktopLogFlushTimer = null
    }
    void flushDesktopLogBufferAsync()

    return
  }

  scheduleDesktopLogFlush()
}

function openExternalUrl(rawUrl) {
  const raw = String(rawUrl || '').trim()
  if (!raw) return false

  let parsed
  try {
    parsed = new URL(raw)
  } catch {
    return false
  }

  // `file://` URLs come from the artifacts panel (the renderer can't open
  // them itself because Chromium blocks file:// navigation from the app
  // origin). Hand them to `shell.openPath`, which dispatches to the OS
  // file association. If the OS can't open it (`error` is a non-empty
  // string), fall back to revealing the file in the system file manager.
  if (parsed.protocol === 'file:') {
    let localPath
    try {
      localPath = fileURLToPath(parsed.toString())
    } catch {
      return false
    }

    void shell
      .openPath(localPath)
      .then(error => {
        if (!error) {
          return
        }

        rememberLog(`[file] openPath failed: ${error}; revealing in folder instead`)

        try {
          shell.showItemInFolder(localPath)
        } catch (revealError) {
          rememberLog(`[file] showItemInFolder failed: ${revealError.message}`)
        }
      })
      .catch(error => rememberLog(`[file] openPath rejected: ${error.message}`))

    return true
  }

  if (!['http:', 'https:', 'mailto:'].includes(parsed.protocol)) {
    return false
  }

  const url = parsed.toString()

  shell.openExternal(url).catch(error => rememberLog(`[link] openExternal failed: ${error.message}`))

  return true
}

function clampBootProgress(value) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return 0
  return Math.max(0, Math.min(100, Math.round(numeric)))
}

function broadcastBootProgress() {
  sendToMain(mainWindow, 'deskagent:boot-progress', bootProgressState)
}

function updateBootProgress(update, options = {}) {
  const nextProgressRaw =
    typeof update.progress === 'number' ? clampBootProgress(update.progress) : bootProgressState.progress
  const nextProgress = options.allowDecrease ? nextProgressRaw : Math.max(bootProgressState.progress, nextProgressRaw)

  bootProgressState = {
    ...bootProgressState,
    ...update,
    error: update.error === undefined ? bootProgressState.error : update.error,
    progress: nextProgress,
    timestamp: Date.now()
  }

  if (update.message) {
    rememberLog(`[boot] ${update.message}`)
  }

  broadcastBootProgress()
}

async function advanceBootProgress(phase, message, progress) {
  updateBootProgress({
    phase,
    message,
    progress,
    running: true,
    error: null
  })
}

function unpackedPathFor(filePath) {
  return filePath.replace(/app\.asar(?=$|[\\/])/, 'app.asar.unpacked')
}

// Resolve the renderer bundle entry point. In a packaged build the
// `dist/index.html` is the SPA shell served by the local backend, and
// `dist/` lives under `app.asar.unpacked/` so the bundled server can
// serve it as static files.
function resolveWebDist() {
  const override = process.env.DESKAGENT_DESKTOP_WEB_DIST
  if (override && directoryExists(path.resolve(override))) return path.resolve(override)

  const unpackedDist = path.join(unpackedPathFor(APP_ROOT), 'dist')
  if (directoryExists(unpackedDist)) return unpackedDist

  // Final fallback: APP_ROOT/dist. When packaged with asar:true this lives
  // INSIDE app.asar — not a servable filesystem directory. If we still land
  // here while packaged, log it so the cause isn't silent.
  const fallback = path.join(APP_ROOT, 'dist')
  if (IS_PACKAGED && /app\.asar(?=$|[\\/])/.test(fallback) && !directoryExists(fallback)) {
    rememberLog(
      `[web-dist] dashboard frontend dir resolved to an asar-internal path that ` +
        `is not a real directory: ${fallback}. Static routes will 404. ` +
        'Ensure dist/** is unpacked (asarUnpack) or set DESKAGENT_DESKTOP_WEB_DIST.'
    )
  }
  return fallback
}

function resolveRendererIndex() {
  const candidates = [path.join(APP_ROOT, 'dist', 'index.html'), path.join(resolveWebDist(), 'index.html')]
  const found = candidates.find(fileExists)
  if (found) return found
  // Nothing on disk. A packaged build with no renderer bundle blank-pages with
  // a bare ERR_FILE_NOT_FOUND. Surface the cause and the fix before Electron
  // loads the missing file.
  rememberLog(
    `[renderer] index.html not found — the desktop app was packaged without a ` +
      'renderer bundle. Tried: ' +
      candidates.join(', ') +
      '. Rebuild via the Tauri DeskAgent-Setup installer.'
  )
  return candidates[0]
}

// Resolve the canonical DeskAgent version. With the Tauri DeskAgent-Setup owning
// install/update, the desktop shell IS the canonical DeskAgent version for
// user-visible purposes (the embedded deskagent_cli module is no longer shipped
// inside the desktop bundle — see installer/CLAUDE.md).
function resolveDeskAgentVersion() {
  return app.getVersion()
}

function fetchJson(url, token, options = {}) {
  return new Promise((resolve, reject) => {
    const body = options.body === undefined ? undefined : Buffer.from(JSON.stringify(options.body))
    const parsed = new URL(url)
    const client = parsed.protocol === 'https:' ? https : http
    const timeoutMs = resolveTimeoutMs(options.timeoutMs, DEFAULT_FETCH_TIMEOUT_MS)

    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      reject(new Error(`Unsupported DeskAgent backend URL protocol: ${parsed.protocol}`))
      return
    }

    const req = client.request(
      parsed,
      {
        method: options.method || 'GET',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...(body ? { 'Content-Length': String(body.length) } : {})
        }
      },
      res => {
        const chunks = []
        res.on('error', reject)
        res.on('data', chunk => chunks.push(chunk))
        res.on('end', () => {
          const text = Buffer.concat(chunks).toString('utf8')
          if ((res.statusCode || 500) >= 400) {
            reject(new Error(`${res.statusCode} ${parsed.pathname}: ${text || res.statusMessage}`))
            return
          }
          if (!text) {
            resolve(null)
            return
          }
          // A 2xx response whose body is HTML means the request fell through
          // to the SPA index.html (e.g. an unregistered /api path). JSON.parse
          // would throw an opaque `Unexpected token '<'` here, so surface a
          // clear diagnostic with the offending URL instead.
          const looksHtml = /^\s*<(?:!doctype|html)/i.test(text)
          const contentType = String(res.headers['content-type'] || '')
          if (looksHtml || contentType.includes('text/html')) {
            reject(
              new Error(
                `Expected JSON from ${url} but got HTML (status ${res.statusCode}). ` +
                  'The endpoint is likely missing on the DeskAgent backend.'
              )
            )
            return
          }
          try {
            resolve(JSON.parse(text))
          } catch {
            reject(new Error(`Invalid JSON from ${url} (status ${res.statusCode}): ${text.slice(0, 200)}`))
          }
        })
      }
    )

    req.on('error', reject)
    req.setTimeout(timeoutMs, () => {
      req.destroy(new Error(`Timed out connecting to DeskAgent backend after ${timeoutMs}ms`))
    })
    if (body) req.write(body)
    req.end()
  })
}

function filenameFromUrl(rawUrl, fallback = 'image') {
  try {
    const parsed = new URL(rawUrl)
    const base = path.basename(decodeURIComponent(parsed.pathname || ''))
    return base && base.includes('.') ? base : fallback
  } catch {
    return fallback
  }
}

async function resourceBufferFromUrl(rawUrl) {
  if (!rawUrl) throw new Error('Missing URL')
  if (rawUrl.startsWith('data:')) {
    const match = rawUrl.match(/^data:([^;,]+)?(;base64)?,(.*)$/s)
    if (!match) throw new Error('Invalid data URL')
    const mimeType = match[1] || 'application/octet-stream'
    const encoded = match[3] || ''
    const buffer = match[2] ? Buffer.from(encoded, 'base64') : Buffer.from(decodeURIComponent(encoded), 'utf8')
    return { buffer, mimeType }
  }
  if (rawUrl.startsWith('file:')) {
    const filePath = fileURLToPath(rawUrl)
    const buffer = await fs.promises.readFile(filePath)
    return { buffer, mimeType: mimeTypeForPath(filePath) }
  }

  const parsed = new URL(rawUrl)
  const client = parsed.protocol === 'https:' ? https : http
  return new Promise((resolve, reject) => {
    const req = client.get(parsed, res => {
      if ((res.statusCode || 500) >= 400) {
        reject(new Error(`Failed to fetch ${rawUrl}: ${res.statusCode}`))
        res.resume()
        return
      }
      const chunks = []
      res.on('error', reject)
      res.on('data', chunk => chunks.push(chunk))
      res.on('end', () => {
        resolve({
          buffer: Buffer.concat(chunks),
          mimeType: res.headers['content-type'] || 'application/octet-stream'
        })
      })
    })
    req.on('error', reject)
  })
}

async function copyImageFromUrl(rawUrl) {
  const { buffer } = await resourceBufferFromUrl(rawUrl)
  const image = nativeImage.createFromBuffer(buffer)
  if (image.isEmpty()) throw new Error('Could not read image')
  clipboard.writeImage(image)
}

async function saveImageFromUrl(rawUrl) {
  const { buffer, mimeType } = await resourceBufferFromUrl(rawUrl)
  const fallbackName = filenameFromUrl(rawUrl, `image${extensionForMimeType(mimeType) || '.png'}`)
  const result = await dialog.showSaveDialog(mainWindow, {
    title: 'Save Image',
    defaultPath: fallbackName
  })
  if (result.canceled || !result.filePath) return false
  await fs.promises.writeFile(result.filePath, buffer)
  return true
}

async function writeComposerImage(buffer, ext = '.png') {
  const rawExt = String(ext || '.png')
    .trim()
    .toLowerCase()
  const normalizedExt = rawExt.startsWith('.') ? rawExt : `.${rawExt}`
  const safeExt = /^\.[a-z0-9]{1,5}$/.test(normalizedExt) ? normalizedExt : '.png'
  const dir = path.join(app.getPath('userData'), 'composer-images')
  await fs.promises.mkdir(dir, { recursive: true })
  const stamp = new Date().toISOString().replace(/[:.]/g, '-').replace('T', '_').replace('Z', '')
  const random = crypto.randomBytes(3).toString('hex')
  const filePath = path.join(dir, `composer_${stamp}_${random}${safeExt}`)
  await fs.promises.writeFile(filePath, buffer)
  return filePath
}

async function waitForDeskAgent(baseUrl, token) {
  const deadline = Date.now() + 45_000
  let lastError = null

  while (Date.now() < deadline) {
    try {
      await fetchJson(`${baseUrl}/health`, token)
      return
    } catch (error) {
      lastError = error
      await sleep(500)
    }
  }

  throw new Error(`DeskAgent backend did not become ready: ${lastError?.message || 'timeout'}`)
}

function getWindowButtonPosition() {
  if (!IS_MAC) return null
  return mainWindow?.getWindowButtonPosition?.() || WINDOW_BUTTON_POSITION
}

function getNativeOverlayWidth() {
  // macOS reports traffic-light coords via windowButtonPosition; the
  // titlebarOverlay there doesn't reserve right-edge space. Windows
  // renders the native window-controls overlay on the right, so the renderer
  // needs to inset its right cluster by this much to clear them.
  return IS_MAC ? 0 : NATIVE_OVERLAY_BUTTON_WIDTH
}

function getWindowState() {
  return {
    isFullscreen: Boolean(mainWindow?.isFullScreen?.()),
    nativeOverlayWidth: getNativeOverlayWidth(),
    windowButtonPosition: getWindowButtonPosition()
  }
}

// Lightweight equality check used by ensureBackend() to detect window-state
// changes that should refresh the cached connection snapshot.
function sameWindowButtonPosition(a, b) {
  return !!a && !!b && a.x === b.x && a.y === b.y
}

function sendClosePreviewRequested() {
  sendToMain(mainWindow, 'deskagent:close-preview-requested')
}

// Tell the renderer the machine just woke. Sleep silently drops the
// renderer's WebSocket to the local backend; the renderer reconnects on this
// signal so the chat composer doesn't stay stuck on "Starting DeskAgent...".
function sendPowerResume() {
  sendToMain(mainWindow, 'deskagent:power-resume')
}

let powerResumeRegistered = false

function registerPowerResumeListeners() {
  if (powerResumeRegistered) return
  powerResumeRegistered = true
  try {
    // 'resume' covers sleep/wake; 'unlock-screen' covers lock/unlock without a
    // full suspend. Either can drop an idle socket.
    powerMonitor.on('resume', sendPowerResume)
    powerMonitor.on('unlock-screen', sendPowerResume)
  } catch {
    // powerMonitor is unavailable before app 'ready' on some platforms; the
    // caller registers after 'ready', so this should not normally throw.
  }
}

function getAppIconPath() {
  return APP_ICON_PATHS.find(fileExists)
}

function sendWindowStateChanged(nextIsFullscreen) {
  if (!mainWindow || mainWindow.isDestroyed()) return
  const state = getWindowState()

  if (typeof nextIsFullscreen === 'boolean') {
    state.isFullscreen = nextIsFullscreen
  }

  sendToMain(mainWindow, 'deskagent:window-state-changed', state)
}

function buildApplicationMenu() {
  const template = []
  if (IS_MAC) {
    template.push({
      label: APP_NAME,
      submenu: [
        { label: `About ${APP_NAME}`, click: () => showAboutPanelFresh() },
        { type: 'separator' },
        { role: 'services' },
        { type: 'separator' },
        { role: 'hide' },
        { role: 'hideOthers' },
        { role: 'unhide' },
        { type: 'separator' },
        { role: 'quit' }
      ]
    })
  }

  template.push({
    label: 'File',
    submenu: [
      IS_MAC
        ? {
            accelerator: 'CommandOrControl+W',
            click: () => {
              if (previewShortcutActive) {
                sendClosePreviewRequested()
              } else {
                mainWindow?.close()
              }
            },
            label: 'Close'
          }
        : { role: 'quit' }
    ]
  })
  template.push({
    label: 'Edit',
    submenu: [
      { role: 'undo' },
      { role: 'redo' },
      { type: 'separator' },
      { role: 'cut' },
      { role: 'copy' },
      { role: 'paste' },
      { role: 'delete' },
      { role: 'selectAll' }
    ]
  })
  template.push({
    label: 'View',
    submenu: [
      { role: 'reload' },
      { role: 'forceReload' },
      { role: 'toggleDevTools' },
      { type: 'separator' },
      {
        label: 'Actual Size',
        accelerator: 'CommandOrControl+0',
        click: () => {
          setAndPersistZoomLevel(mainWindow, 0)
        }
      },
      {
        label: 'Zoom In',
        accelerator: 'CommandOrControl+Plus',
        click: () => {
          if (mainWindow && !mainWindow.isDestroyed()) {
            setAndPersistZoomLevel(mainWindow, mainWindow.webContents.getZoomLevel() + 0.1)
          }
        }
      },
      {
        label: 'Zoom Out',
        accelerator: 'CommandOrControl+-',
        click: () => {
          if (mainWindow && !mainWindow.isDestroyed()) {
            setAndPersistZoomLevel(mainWindow, mainWindow.webContents.getZoomLevel() - 0.1)
          }
        }
      },
      { type: 'separator' },
      { role: 'togglefullscreen' }
    ]
  })
  template.push({
    label: 'Window',
    submenu: IS_MAC
      ? [{ role: 'minimize' }, { role: 'zoom' }, { role: 'front' }]
      : [{ role: 'minimize' }, { role: 'close' }]
  })

  return Menu.buildFromTemplate(template)
}

function toggleDevTools(window) {
  // DevTools is enabled in packaged builds so users can diagnose renderer
  // issues without needing a dev build. Trade-off: tiny attack surface
  // increase versus a much better support story when WS connection or
  // CSP issues surface in the field.
  const { webContents } = window
  if (webContents.isDevToolsOpened()) {
    webContents.closeDevTools()
  } else {
    webContents.openDevTools({ mode: 'detach' })
  }
}

function installDevToolsShortcut(window) {
  // F12 / Cmd+Opt+I works in both dev and packaged builds.
  window.webContents.on('before-input-event', (event, input) => {
    const key = input.key.toLowerCase()
    const isInspectShortcut =
      input.key === 'F12' ||
      (IS_MAC && input.meta && input.alt && key === 'i') ||
      (!IS_MAC && input.control && input.shift && key === 'i')
    if (!isInspectShortcut) return
    event.preventDefault()
    toggleDevTools(window)
  })
}

function installPreviewShortcut(window) {
  window.webContents.on('before-input-event', (event, input) => {
    const key = String(input.key || '').toLowerCase()
    const isPreviewCloseShortcut = key === 'w' && (IS_MAC ? input.meta : input.control) && !input.alt && !input.shift

    if (!isPreviewCloseShortcut || !previewShortcutActive) return

    event.preventDefault()
    sendClosePreviewRequested()
  })
}

// Zoom level is persisted in the renderer's own localStorage (per-origin,
// survives reloads/restarts) rather than a main-process JSON file. The main
// process owns setZoomLevel, so we mirror each change into localStorage and
// read it back on did-finish-load to re-apply after reloads or crash recovery.
const ZOOM_STORAGE_KEY = 'deskagent:desktop:zoomLevel'

function clampZoomLevel(value) {
  if (!Number.isFinite(value)) return 0
  return Math.min(Math.max(value, -9), 9)
}

function setAndPersistZoomLevel(window, zoomLevel) {
  if (!window || window.isDestroyed()) return
  const next = clampZoomLevel(zoomLevel)
  window.webContents.setZoomLevel(next)
  window.webContents
    .executeJavaScript(
      `try { localStorage.setItem(${JSON.stringify(ZOOM_STORAGE_KEY)}, ${JSON.stringify(String(next))}) } catch {}`
    )
    .catch(error => rememberLog(`[zoom] persist failed: ${error?.message || error}`))
}

function restorePersistedZoomLevel(window) {
  if (!window || window.isDestroyed()) return
  window.webContents
    .executeJavaScript(
      `(() => { try { return localStorage.getItem(${JSON.stringify(ZOOM_STORAGE_KEY)}) } catch { return null } })()`
    )
    .then(stored => {
      if (stored == null || !window || window.isDestroyed()) return
      const level = clampZoomLevel(Number(stored))
      window.webContents.setZoomLevel(level)
    })
    .catch(error => rememberLog(`[zoom] restore failed: ${error?.message || error}`))
}

function installZoomShortcuts(window) {
  // Override Ctrl/Cmd + +/-/0 with half the default zoom step (0.1 vs 0.2).
  // The menu items handle this on macOS (where the menu is always present),
  // but on Windows the menu is null and Chromium's default handler
  // would use the full 0.2 step, so we intercept here for consistency.
  const ZOOM_STEP = 0.1
  window.webContents.on('before-input-event', (event, input) => {
    const mod = IS_MAC ? input.meta : input.control
    if (!mod || input.alt || input.shift) return

    const key = input.key
    if (key === '0') {
      event.preventDefault()
      setAndPersistZoomLevel(window, 0)
    } else if (key === '=' || key === '+') {
      event.preventDefault()
      setAndPersistZoomLevel(window, window.webContents.getZoomLevel() + ZOOM_STEP)
    } else if (key === '-') {
      event.preventDefault()
      setAndPersistZoomLevel(window, window.webContents.getZoomLevel() - ZOOM_STEP)
    }
  })
}

function installContextMenu(window) {
  window.webContents.on('context-menu', (_event, params) => {
    const template = []
    const hasSelection = Boolean(params.selectionText?.trim())
    const hasImage = params.mediaType === 'image' && Boolean(params.srcURL)
    const hasLink = Boolean(params.linkURL)
    const isEditable = Boolean(params.isEditable)

    if (hasImage) {
      template.push(
        {
          label: 'Open Image',
          click: () => {
            if (params.srcURL && !params.srcURL.startsWith('data:')) {
              openExternalUrl(params.srcURL)
            }
          },
          enabled: !params.srcURL.startsWith('data:')
        },
        {
          label: 'Copy Image',
          click: () => {
            void copyImageFromUrl(params.srcURL).catch(error => rememberLog(`Copy image failed: ${error.message}`))
          }
        },
        {
          label: 'Copy Image Address',
          click: () => clipboard.writeText(params.srcURL)
        },
        {
          label: 'Save Image As...',
          click: () => {
            void saveImageFromUrl(params.srcURL).catch(error => rememberLog(`Save image failed: ${error.message}`))
          }
        }
      )
    }

    if (hasLink) {
      if (template.length) template.push({ type: 'separator' })
      template.push(
        {
          label: 'Open Link',
          click: () => openExternalUrl(params.linkURL)
        },
        {
          label: 'Copy Link',
          click: () => clipboard.writeText(params.linkURL)
        }
      )
    }

    // Spell-check suggestions for the misspelled word under the caret.
    // Chromium surfaces them on `params.dictionarySuggestions`; we offer the
    // top 5 plus a "Add to dictionary" affordance.
    const suggestions = Array.isArray(params.dictionarySuggestions) ? params.dictionarySuggestions : []

    if (isEditable && params.misspelledWord && suggestions.length > 0) {
      if (template.length) template.push({ type: 'separator' })

      for (const suggestion of suggestions.slice(0, 5)) {
        template.push({
          label: suggestion,
          click: () => window.webContents.replaceMisspelling(suggestion)
        })
      }

      template.push({ type: 'separator' })
      template.push({
        label: 'Add to dictionary',
        click: () => window.webContents.session.addWordToSpellCheckerDictionary(params.misspelledWord)
      })
    }

    if (hasSelection || isEditable) {
      if (template.length) template.push({ type: 'separator' })
      if (isEditable) {
        template.push(
          { role: 'cut', enabled: params.editFlags.canCut },
          { role: 'copy', enabled: params.editFlags.canCopy },
          { role: 'paste', enabled: params.editFlags.canPaste },
          { type: 'separator' },
          { role: 'selectAll', enabled: params.editFlags.canSelectAll }
        )
      } else {
        template.push({ role: 'copy', enabled: params.editFlags.canCopy })
      }
    }

    if (!template.length) {
      template.push({ role: 'selectAll' })
    }

    Menu.buildFromTemplate(template).popup({ window })
  })
}

// Microphone capture for the voice composer. The renderer drives mic access
// through getUserMedia, which Chromium gates behind these two session hooks.
//
// The naive `details.mediaTypes.includes('audio')` check works on macOS but
// breaks on Windows: Chromium frequently fires the mic permission request with
// an empty/undefined `mediaTypes`, so the strict check denies it and
// getUserMedia throws NotAllowedError ("Microphone permission was denied").
// We therefore treat an audio-capture request as allowed whenever it's the
// 'media'/'audioCapture' permission AND mediaTypes either includes 'audio' OR
// is empty/absent (the Windows case). Video is still denied.
function isAudioCapturePermission(permission, details) {
  if (permission === 'audioCapture') {
    return true
  }
  if (permission !== 'media') {
    return false
  }
  const mediaTypes = details?.mediaTypes
  if (!Array.isArray(mediaTypes) || mediaTypes.length === 0) {
    // Windows: mediaTypes is often empty for a mic request. Don't deny on
    // missing metadata. (A video request would carry mediaTypes:['video'].)
    return true
  }
  return mediaTypes.includes('audio') && !mediaTypes.includes('video')
}

function installMediaPermissions() {
  // Async request handler: the prompt-style path (most platforms).
  session.defaultSession.setPermissionRequestHandler((_webContents, permission, callback, details) => {
    callback(isAudioCapturePermission(permission, details))
  })

  // Synchronous check handler: Chromium consults this for getUserMedia on
  // Windows in addition to (or instead of) the request handler. Without it,
  // the check defaults to false and the mic is denied before the request
  // handler ever runs.
  session.defaultSession.setPermissionCheckHandler((_webContents, permission, _origin, details) => {
    if (permission === 'media' || permission === 'audioCapture') {
      // details.mediaType is a single string here (not the mediaTypes array).
      const mediaType = details?.mediaType
      if (mediaType === 'video') {
        return false
      }

      return true
    }

    return false
  })
}

// Auto-update the inner Electron shell against the backend update server.
// The flow:
//   1. main process auto-checks ~30s after launch (renderer hasn't loaded
//      the user-facing flow yet and the backend health probe has settled).
//   2. autoUpdater events are forwarded to the renderer as `deskagent:update-event`
//      by ipc/update.cjs; that module also owns the user-action handlers
//      (check / download / install).
//   3. autoDownload is OFF — the renderer shows a "Restart to update"
//      dialog and only then asks main to download + install.
//   4. When the desktop binary is downloaded (update-downloaded), main
//      starts Phase 1 of the runner update: download + verify the runner
//      wheel + server.py to $DESKAGENT_HOME/runner.staging/. The renderer only
//      enables the "Restart now" button AFTER phase 1 completes. This
//      avoids the brick state where a new Electron is launched but the
//      runner update cannot complete (network down, etc.).
//   5. After Squirrel swaps to the new desktop, on next launch main
//      runs Phase 2 (installPending) which `pip install --upgrade` the
//      wheel in place into the existing venv and overwrites server.py.
//
// In dev mode (`!app.isPackaged`) the IPC module short-circuits to
// no-ops; we don't call checkForUpdates here either to avoid the
// "no valid app update config" error from a non-packaged run.
const UPDATE_INITIAL_CHECK_DELAY_MS = 30_000

let runnerUpdaterSingleton = null

function getRunnerUpdater() {
  if (runnerUpdaterSingleton) return runnerUpdaterSingleton
  runnerUpdaterSingleton = new RunnerUpdater({
    bridgeDeps,
    getMainWindow: () => mainWindow,
    sendToMain
  })
  return runnerUpdaterSingleton
}

function setupAutoUpdater() {
  if (!app.isPackaged) return

  const { autoUpdater } = require('electron-updater')

  autoUpdater.autoDownload = false
  autoUpdater.autoInstallOnAppQuit = false
  autoUpdater.logger = log

  const updateBaseUrl = resolveNormalizedBackendUrl(DESKAGENT_HOME) + '/api/update'
  const publicKeyPath = getBundledPublicKeyPath()

  if (!publicKeyPath) {
    log.warn('update.pub not found in extraResources; runner signature verification will fail')
  }

  autoUpdater.setFeedURL({
    provider: 'generic',
    url: updateBaseUrl
  })

  // Phase 1: when Squirrel finishes downloading the new desktop binary,
  // start the runner download in the background. The user can already
  // see the "Restart now" dialog while phase 1 is running; the renderer
  // gates the actual restart on `runner-ready` so we never boot a new
  // Electron that has no runner asset staged.
  autoUpdater.on('update-downloaded', info => {
    log.info('desktop update downloaded; starting runner prefetch', info?.version)
    getRunnerUpdater()
      .prefetchRunnerAssets({
        version: info?.version || app.getVersion(),
        updateBaseUrl,
        publicKeyPath
      })
      .catch(err => {
        log.warn('runner prefetch failed:', err?.message || err)
      })
  })

  // Stagger the initial check so it doesn't race the backend health probe
  // or the first-paint. `.unref()` keeps this timer from holding the
  // event loop alive at shutdown.
  const timer = setTimeout(() => {
    autoUpdater.checkForUpdates().catch(error => {
      log.warn('initial update check failed:', error?.message || error)
    })
  }, UPDATE_INITIAL_CHECK_DELAY_MS)
  if (typeof timer.unref === 'function') timer.unref()
}

// Public key for verifying the runner manifest's RSA signature. electron-
// builder ships `update.pub` under extraResources; we also check the
// repo's scripts/secrets/ for development (dev mode) runs.
function getBundledPublicKeyPath() {
  try {
    const candidates = [
      path.join(process.resourcesPath || '', 'update.pub'),
      path.join(__dirname, '..', 'update.pub'),
      path.join(__dirname, 'update.pub'),
      // dev mode: repo/scripts/secrets/update.pub
      path.join(__dirname, '..', '..', '..', 'scripts', 'secrets', 'update.pub')
    ]
    for (const candidate of candidates) {
      if (fs.existsSync(candidate)) return candidate
    }
  } catch {
    // best effort
  }
  return null
}

async function resolveRemoteBackend() {
  const url = resolveNormalizedBackendUrl(DESKAGENT_HOME)
  return url ? { baseUrl: url } : null
}

// Forward-declared; bridgeDeps assigns the real impl after backendSession is
// require()d at the bottom of this file. Default to no token so the boot
// surfaces a clean auth failure rather than leaking the prior env-var path.
let getAuthToken = () => null

// Cached backend connection (health-checked once, reused thereafter). Cleared
// on login/logout so the next ensureBackend() re-resolves with the fresh JWT.
let cachedBackend = null

function resetBackendCache() {
  cachedBackend = null
}

// WS-only JWT so the renderer never holds the long-lived access token (ARCH §7.1).
async function mintWsTicket(baseUrl, token) {
  if (!token) return null
  try {
    const res = await fetchJson(`${baseUrl}/api/user/ws-ticket`, token, { method: 'POST', timeoutMs: 5000 })
    return res?.access_token || null
  } catch (error) {
    rememberLog(`[ws-ticket] mint failed: ${error?.message || error}`)
    return null
  }
}

async function ensureBackend() {
  if (cachedBackend) {
    const token = getAuthToken()
    const tokenChanged = token !== cachedBackend.token

    // Cheap path: token is the only state that moves without a window-state
    // event. Window state feeds `deskagent:connection`; `deskagent:window-state-changed`
    // is a separate stream that doesn't refresh the cache, so on a stable
    // token we still need to detect moves / fullscreen toggles here.
    if (
      !tokenChanged &&
      cachedBackend.isFullscreen === Boolean(mainWindow?.isFullScreen?.()) &&
      cachedBackend.nativeOverlayWidth === getNativeOverlayWidth() &&
      sameWindowButtonPosition(getWindowButtonPosition(), cachedBackend.windowButtonPosition)
    ) {
      return cachedBackend
    }

    const liveWindowState = getWindowState()
    const wsBase = cachedBackend.baseUrl.replace(/^http/, 'ws')
    const wsTicket = await mintWsTicket(cachedBackend.baseUrl, token)
    cachedBackend = {
      ...cachedBackend,
      ...liveWindowState,
      token,
      wsUrl: wsTicket ? `${wsBase}/api/chat/ws?ticket=${wsTicket}` : `${wsBase}/api/chat/ws`
    }
    return cachedBackend
  }

  await advanceBootProgress('backend.resolve', 'Resolving DeskAgent backend', 8)
  const remote = await resolveRemoteBackend()
  if (remote) {
    const token = getAuthToken()
    await advanceBootProgress('backend.remote', `Connecting to remote DeskAgent backend at ${remote.baseUrl}`, 24)
    await waitForDeskAgent(remote.baseUrl, token)
    updateBootProgress({
      phase: 'backend.ready',
      message: 'Remote DeskAgent backend is ready',
      progress: 94,
      running: true,
      error: null
    })
    const wsBase = remote.baseUrl.replace(/^http/, 'ws')
    const wsTicket = await mintWsTicket(remote.baseUrl, token)
    cachedBackend = {
      baseUrl: remote.baseUrl,
      mode: 'remote',
      source: 'env',
      authMode: 'token',
      token,
      wsUrl: wsTicket ? `${wsBase}/api/chat/ws?ticket=${wsTicket}` : `${wsBase}/api/chat/ws`,
      logs: deskagentLog.slice(-80),
      ...getWindowState()
    }
    return cachedBackend
  }

  throw new Error('No remote DeskAgent backend configured.')
}

// Renderer entry URL stamped with a window role so the shared bundle branches
// at the root: `sprite` (transparent companion surface) vs `tool` (framed
// Login / Settings). A query param keeps it independent of the HashRouter.
function rendererUrlFor(role) {
  const suffix = `?role=${role}`
  return DEV_SERVER ? DEV_SERVER + suffix : pathToFileURL(resolveRendererIndex()).toString() + suffix
}

// Handlers common to every BrowserWindow: devtools shortcut, external-link
// interception, crash-loop-bounded reload, console-error logging, and the
// close-to-tray interceptor (tray.cjs contract: re-apply on every window).
function installStandardWindowHandlers(win) {
  installDevToolsShortcut(win)
  win.webContents.setWindowOpenHandler(details => {
    openExternalUrl(details.url)

    return { action: 'deny' }
  })
  win.webContents.on('will-navigate', (event, url) => {
    if ((DEV_SERVER && url.startsWith(DEV_SERVER)) || (!DEV_SERVER && url.startsWith('file:'))) {
      return
    }

    event.preventDefault()
    openExternalUrl(url)
  })
  win.webContents.on('render-process-gone', (_event, details) => {
    rememberLog(`[renderer] render-process-gone reason=${details?.reason} exitCode=${details?.exitCode}`)

    if (details?.reason === 'crashed' || details?.reason === 'oom') {
      const now = Date.now()
      rendererReloadTimes = rendererReloadTimes.filter(t => now - t < RENDERER_RELOAD_WINDOW_MS)

      if (rendererReloadTimes.length >= RENDERER_RELOAD_MAX) {
        rememberLog(
          `[renderer] suppressing reload: ${rendererReloadTimes.length} crashes within ${RENDERER_RELOAD_WINDOW_MS}ms (likely a crash loop)`
        )

        return
      }

      rendererReloadTimes.push(now)
      setImmediate(() => {
        if (!win || win.isDestroyed()) return
        try {
          win.webContents.reload()
        } catch (err) {
          rememberLog(`[renderer] reload after crash failed: ${err?.message || err}`)
        }
      })
    }
  })
  win.webContents.on('unresponsive', () => rememberLog('[renderer] webContents became unresponsive'))

  // Electron always passes the event first. The canonical (Electron 36+) shape
  // is (event, messageDetails); the deprecated positional shape is
  // (event, level, message, line, sourceId). Handle both. `level` is numeric
  // (0..3), where 3 === error.
  win.webContents.on('console-message', (_event, detailsOrLevel, message, line, sourceId) => {
    const details = detailsOrLevel && typeof detailsOrLevel === 'object' ? detailsOrLevel : null
    const level = details ? details.level : detailsOrLevel

    if (level !== 3) return

    const text = details ? details.message : message
    const src = details ? details.sourceUrl : sourceId
    const lineNo = details ? details.lineNumber : line
    rememberLog(`[renderer console] ${text} (${src}:${lineNo})`)
  })
  installCloseInterceptor(win)
}

// On-demand framed window hosting Login (unauthenticated) and Settings
// (authenticated) — REST-only, it never boots the gateway. Created lazily by
// showToolWindow (egg-crack gesture, tray Settings / Sign-in).
function createToolWindow() {
  const icon = getAppIconPath()
  toolWindow = new BrowserWindow({
    width: 1220,
    height: 800,
    minWidth: 400,
    minHeight: 620,
    title: 'DeskAgent',
    // Frameless title bar on every platform so the renderer can paint the
    // titlebar tools flush with the top edge. On Windows titleBarOverlay
    // paints native min/max/close in the top-right of the renderer; on macOS it
    // reserves a content inset alongside the traffic lights.
    titleBarStyle: 'hidden',
    titleBarOverlay: getTitleBarOverlayOptions(),
    trafficLightPosition: IS_MAC ? WINDOW_BUTTON_POSITION : undefined,
    vibrancy: IS_MAC ? 'sidebar' : undefined,
    icon,
    backgroundColor: '#f7f7f7',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      webviewTag: true,
      sandbox: true,
      nodeIntegration: false,
      devTools: true,
      backgroundThrottling: false
    }
  })

  if (IS_MAC) {
    toolWindow.setWindowButtonPosition?.(WINDOW_BUTTON_POSITION)
  }

  if (!IS_MAC) {
    if (!nativeThemeListenerInstalled) {
      nativeThemeListenerInstalled = true
      nativeTheme.on('updated', () => {
        toolWindow?.setTitleBarOverlay?.(getTitleBarOverlayOptions())
      })
    }
  }

  toolWindow.on('will-enter-full-screen', () => sendWindowStateChanged(true))
  toolWindow.on('enter-full-screen', () => sendWindowStateChanged(true))
  toolWindow.on('will-leave-full-screen', () => sendWindowStateChanged(false))
  toolWindow.on('leave-full-screen', () => sendWindowStateChanged(false))

  installPreviewShortcut(toolWindow)
  installZoomShortcuts(toolWindow)
  installContextMenu(toolWindow)
  installStandardWindowHandlers(toolWindow)

  toolWindow.loadURL(rendererUrlFor('tool'))

  toolWindow.webContents.once('did-finish-load', () => {
    restorePersistedZoomLevel(toolWindow)
  })
}

// The transparent screen-sized always-on-top window — the sole resident main
// window. The companion, chat dialog, onboarding, and proactive bubbles all
// render as absolutely positioned overlays inside it; non-interactive regions
// are click-through (setIgnoreMouseEvents + forward). Remote displays (X11 /
// VNC / RDP) can't composite transparency, so the sprite degrades to a
// non-transparent window there (no compositor degradation is a known
// limitation, see client/README.md).
const SPRITE_TRANSPARENT = !REMOTE_DISPLAY_REASON

function applySpriteBounds() {
  if (!mainWindow || mainWindow.isDestroyed()) return
  mainWindow.setBounds(screen.getPrimaryDisplay().workArea)
}

function createSpriteWindow() {
  const icon = getAppIconPath()
  mainWindow = new BrowserWindow({
    width: 480,
    height: 320,
    frame: false,
    transparent: SPRITE_TRANSPARENT,
    resizable: false,
    movable: false,
    hasShadow: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    show: false,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      devTools: true,
      backgroundThrottling: false
    }
  })

  applySpriteBounds()
  // Default click-through with mousemove forwarding so the renderer can detect
  // re-entering an interactive overlay and request capture via the sprite IPC.
  mainWindow.setIgnoreMouseEvents(true, { forward: SPRITE_TRANSPARENT })
  mainWindow.setAlwaysOnTop(true, 'floating')

  if (IS_MAC && icon) {
    app.dock?.setIcon(icon)
  }
  // Re-cover the work area when the display resizes. Registered once (sprite
  // recreation is rare; applySpriteBounds no-ops on a destroyed window anyway).
  if (!spriteBoundsListenerInstalled) {
    spriteBoundsListenerInstalled = true
    screen.on('display-metrics-changed', applySpriteBounds)
  }

  installStandardWindowHandlers(mainWindow)

  mainWindow.loadURL(rendererUrlFor('sprite'))
  mainWindow.webContents.once('did-finish-load', () => {
    broadcastBootProgress()
    // Ambient: appear without stealing focus from whatever the user is doing.
    mainWindow.showInactive()
  })
}

// Create-or-show the framed tool window. The renderer self-selects Login vs
// Settings from $auth, so callers don't distinguish.
function showToolWindow() {
  if (!toolWindow || toolWindow.isDestroyed()) {
    createToolWindow()
    return
  }
  if (toolWindow.isMinimized()) toolWindow.restore()
  if (!toolWindow.isVisible()) {
    if (process.platform === 'win32') toolWindow.setSkipTaskbar(false)
    toolWindow.show()
  }
  toolWindow.focus()
}

function hideToolWindow() {
  if (toolWindow && !toolWindow.isDestroyed()) {
    toolWindow.hide()
    if (process.platform === 'win32') toolWindow.setSkipTaskbar(true)
  }
}

// Auth state is owned per-renderer (two windows = two nanostores). Broadcast
// every login/logout/refresh to BOTH windows so the sprite (which never hosts
// the login form) learns the new session and can boot/teardown its gateway.
function broadcastAuthChanged(snapshot) {
  const authenticated = Boolean(snapshot?.hasToken)
  const payload = { authenticated, snapshot: authenticated ? snapshot : null }
  for (const win of [mainWindow, toolWindow]) {
    if (win && !win.isDestroyed()) win.webContents.send('deskagent:auth:changed', payload)
  }
}

// IPC handlers split out into per-namespace modules under ./ipc. Each module
// takes its own deps (no global bag) so the seam between main.cjs (lifecycle,
// shared state) and the modules (channels) stays explicit. Modules added
// here are the low-coupling ones; auth/runner/chat/preview/connection/
// terminal/images/link-title/profile remain inline until their second-batch
// extraction.
registerSystemIpc({
  ipcMain,
  electron: { app }
})
registerTitlebarIpc({
  ipcMain,
  getToolWindow: () => toolWindow,
  getTitleBarOverlayOptions,
  setRendererTitleBarTheme: theme => {
    rendererTitleBarTheme = theme
  }
})
registerClipboardIpc({
  ipcMain,
  electron: { clipboard },
  writeComposerImage
})
registerExternalIpc()
registerSettingsIpc()
registerFilesIpc({
  ipcMain,
  electron: { dialog, getMainWindow: () => mainWindow },
  hardening: { DATA_URL_READ_MAX_BYTES, TEXT_PREVIEW_SOURCE_MAX_BYTES, resolveReadableFileForIpc },
  mimeTypeForPath,
  previewLanguageByExt: PREVIEW_LANGUAGE_BY_EXT
})
registerOnboardingAudioIpc({
  ipcMain,
  deskagentHome: DESKAGENT_HOME,
  mimeTypeForPath,
  hardening: { resolveReadableFileForIpc }
})
registerConnectionIpc({
  ipcMain,
  ensureBackend,
  resetBackendCache,
  getBootProgressState: () => bootProgressState,
  fetchJson,
  resolvePathTimeoutMs,
  resolveTimeoutMs,
  defaultFetchTimeoutMs: DEFAULT_FETCH_TIMEOUT_MS
})
registerMediaIpc({
  ipcMain,
  ensureBackend,
  deskagentHome: DESKAGENT_HOME,
  // Lazily resolved at media-request time — bridgeDeps.runnerBridge is created
  // by ensureRunnerBridge after session restore, well after this registration.
  getRunnerBridge: () => bridgeDeps.runnerBridge,
  getEnginePrefs: createEnginePrefsCache({ ensureBackend }),
  // Per-call STT/TTS trace: emits one structured line per routing decision
  // (received / local.invoke / local.ok / fallback / cloud.request / done).
  // Wired to rememberLog so [tts#N] / [stt#N] lines flow into both the
  // desktop log file and the dev terminal under `pnpm dev`.
  log: chunk => rememberLog(chunk)
})

// Re-seed the native About panel right before opening it. The value still
// comes from `app.getVersion()`, so it reflects the running process's
// compiled-in version, not any in-flight installer update. macOS only —
// `showAboutPanel()` is a no-op elsewhere, and the other platforms don't use
// this menu item.
function showAboutPanelFresh() {
  app.setAboutPanelOptions({
    applicationName: APP_NAME,
    applicationVersion: app.getVersion(),
    copyright: 'Copyright © 2026 DeskAgent'
  })
  app.showAboutPanel()
}

// The desktop talks to the cloud Backend over REST for login / session. The
// modules live in main/backend-{client,session}.cjs so
// they can be unit-tested without Electron; main.cjs only wires them to
// ipcMain. `deskagent:api` IPC is the general-purpose REST proxy (sessions,
// profiles, configs, models, tools, cron, OAuth, …).

const { createBackendSession } = require('./backend/session.cjs')
const { buildClientContext } = require('./shared/client-context.cjs')
const { resolveNormalizedBackendUrl, resolveBackendUrl } = require('./shared/config.cjs')
const { createRunnerProcess } = require('./runner/process.cjs')
const { createRunnerWsServer } = require('./runner/rpc-ws.cjs')
const { createReverseRpc } = require('./runner/reverse-rpc.cjs')
const { createRunnerBridge } = require('./runner/bridge.cjs')

// Shared mutable state for auth + runner modules. The bridge and backend
// session are both initialized lazily and live across login/logout cycles;
// both modules read and write slots on this object so they stay in sync.
const bridgeDeps = {
  app,
  electronNet,
  deskagentHome: DESKAGENT_HOME,
  safeStorage,
  backendSession: null,
  runnerBridge: null,
  fileExists,
  createBackendSession,
  // Builds the clientContext (platform / arch / release / version / skills)
  // that desktop stamps into the JWT ctx claim at login. Only main process
  // can read process.platform and $DESKAGENT_HOME/skills.
  buildClientContext: () =>
    buildClientContext({
      desktopVersion: resolveDeskAgentVersion(),
      deskagentHome: deskagentHome()
    }),
  atomicWriteFile,
  createRunnerProcess,
  createRunnerWsServer,
  createReverseRpc,
  createRunnerBridge,
  resolveDeskAgentVersion,
  ensureBackendSession: () => {
    if (bridgeDeps.backendSession) return bridgeDeps.backendSession
    bridgeDeps.backendSession = createBackendSession({
      userDataDir: app.getPath('userData'),
      safeStorage,
      appVersion: resolveDeskAgentVersion(),
      fetchImpl: (url, options) => electronNet.fetch(url, options),
      defaultBaseUrl: resolveBackendUrl(DESKAGENT_HOME) || null,
      log: chunk => rememberLog(chunk)
    })
    try {
      bridgeDeps.backendSession.restoreSession()
    } catch (error) {
      rememberLog(`[session] restore failed: ${error.message}`)
    }
    return bridgeDeps.backendSession
  },
  fetchJson,
  rememberLog,
  // Wire forward-declared getAuthToken() to the live backendSession on every resolve.
  rewireAuthToken: () => {
    getAuthToken = () => bridgeDeps.ensureBackendSession().getToken() ?? null
  },
  taggedLogger: prefix => chunk => rememberLog(`${prefix} ${chunk}`),
  getMainWindow: () => mainWindow,
  // The sprite is `mainWindow`; the framed Login/Settings window is
  // `toolWindow`, created on demand.
  getSpriteWindow: () => mainWindow,
  getToolWindow: () => toolWindow,
  showToolWindow,
  hideToolWindow,
  broadcastAuthChanged,
  // Rebuild the tray context menu after auth state changes (login/logout) so
  // the Show/Sign-in + Settings + Log-out label set reflects the live session.
  rebuildTrayMenu: () => rebuildTrayMenu(),
  // Tray / single-instance state. `isQuitting` is flipped in `before-quit`
  // (the only universal quit hook — covers Cmd+Q, tray Quit, and any other
  // teardown path) and consulted by the close interceptor installed by
  // `tray.cjs::installCloseInterceptor` to decide whether to actually
  // destroy the window or just hide it.
  isQuitting: false,
  autoStartBridge: () => autoStartBridge(bridgeDeps),
  autoStopBridge: () => autoStopBridge(bridgeDeps),
  restartRunnerBridge: () => restartRunnerBridge(bridgeDeps),
  resetBackendCache
}

registerAuthIpc({ ipcMain, deps: bridgeDeps })
registerRunnerIpc({ ipcMain, deps: bridgeDeps })
registerRunnerConfigIpc({ ipcMain, deps: bridgeDeps })
registerSkillsIpc({ ipcMain, deps: bridgeDeps, deskagentHome: deskagentHome() })
registerUpdateIpc({
  ipcMain,
  electron: { app },
  sendToMain,
  getMainWindow: () => mainWindow
})

registerSpriteIpc({
  ipcMain,
  deps: { getSpriteWindow: () => mainWindow, getUserDataDir: () => app.getPath('userData') }
})

// Sprite → main: bring up the framed tool window (the egg-crack gesture hands
// the user off to Login; tray Settings reuses the same path for Settings).
ipcMain.handle('deskagent:window:show-tool', async () => {
  showToolWindow()
})

ipcMain.handle('deskagent:runner:get-tools', async () => {
  // Wait up to 5s for the runner bridge so an early hub-page probe doesn't read empty.
  const deadline = Date.now() + 5000
  while (!bridgeDeps.runnerBridge && Date.now() < deadline) {
    await sleep(100)
  }
  if (!bridgeDeps.runnerBridge) {
    return []
  }
  return bridgeDeps.runnerBridge.getTools()
})

bridgeDeps.rewireAuthToken()

// Restore already-logged-in session state after launch.
setTimeout(() => {
  if (bridgeDeps.ensureBackendSession().getSession()?.hasToken) {
    autoStartBridge(bridgeDeps)
  }
}, 200).unref?.()

// One-shot installer → desktop handoff. Runs INSIDE app.whenReady() after
// windows exist (otherwise broadcastAuthChanged() has no listeners and
// the renderer still shows Login on first paint). On success the bridge
// is kicked explicitly — the 200ms timer above has long since fired by
// the time Electron is ready, so it can't see an adopted session.
const { consumeBootstrapSession } = require('./backend/bootstrap-session.cjs')
async function tryConsumeBootstrapSession() {
  try {
    const result = await consumeBootstrapSession({
      deskagentHome: DESKAGENT_HOME,
      fetchImpl: (url, options) => electronNet.fetch(url, options),
      log: chunk => rememberLog(chunk)
    })
    if (result.status !== 'ok' || !result.snapshot) return
    const session = bridgeDeps.ensureBackendSession()
    session.adoptSession(result.snapshot)
    // Mirror the existing login flow: invalidate the cached backend
    // connection so the next ensureBackend() re-resolves with the
    // fresh token, rebuild the tray menu, and broadcast to both windows
    // so the sprite can boot its gateway without showing Login.
    bridgeDeps.resetBackendCache?.()
    bridgeDeps.rebuildTrayMenu?.()
    bridgeDeps.broadcastAuthChanged?.(session.getSession())
    // Bootstrap adoption happens after the 200ms timer — start the bridge
    // ourselves. autoStartBridge is idempotent.
    autoStartBridge(bridgeDeps)
  } catch (error) {
    rememberLog(`[bootstrap-session] consume failed: ${error?.message || error}`)
  }
}

// One-shot: legacy connection.json deprecated, rename to .bak silently
try {
  const legacyPath = path.join(app.getPath('userData'), 'connection.json')
  if (fs.existsSync(legacyPath)) {
    const stamp = new Date().toISOString().replace(/[:.]/g, '-')
    fs.renameSync(legacyPath, legacyPath + '.bak-' + stamp)
  }
} catch {
  /* swallow */
}

app.whenReady().then(async () => {
  if (IS_MAC) {
    Menu.setApplicationMenu(buildApplicationMenu())
  } else {
    Menu.setApplicationMenu(null)
  }
  installMediaPermissions()
  registerMediaProtocol()
  configureSpellChecker()
  registerPowerResumeListeners()
  setupAutoUpdater()
  // Phase 2 of the runner self-update: if a previous-version Electron
  // staged a runner update and wrote a sentinel, install it now. This
  // runs BEFORE createSpriteWindow() so the user lands in a fully-updated
  // state on first paint. installPending is fast (local pip + file
  // copy, no network) so the brief delay is acceptable.
  await getRunnerUpdater()
    .installPending()
    .catch(err => {
      log.warn('runner installPending failed:', err?.message || err)
    })
  createSpriteWindow()

  // Single-instance `second-instance` forwarder must be registered after the
  // sprite window exists so `bridgeDeps.getMainWindow()` resolves. The actual
  // tray icon + close interceptor are installed by `installTray` below.
  registerSingleInstanceForwarder({
    app,
    bridgeDeps,
    rememberLog,
    createWindow: createSpriteWindow
  })

  installTray({
    app,
    Menu,
    Tray,
    nativeImage,
    IS_MAC,
    getAppIconPath,
    rememberLog,
    bridgeDeps,
    createWindow: createSpriteWindow
  })

  // Installer → desktop session handoff. Runs after createSpriteWindow so
  // broadcastAuthChanged() lands on a live window — otherwise the renderer
  // still sees a no-session state on first paint and shows Login.
  // Fire-and-forget: a cold install with an unreachable backend shouldn't
  // hold up first paint for the full 15s refresh timeout.
  tryConsumeBootstrapSession()

  // macOS dock click → recreate or focus the sprite. Mostly dead on macOS
  // because `installTray` hides the dock, but kept so the app still behaves
  // sensibly if a user later unhides the dock (e.g. via `defaults write`).
  app.on('activate', () => {
    const win = bridgeDeps.getMainWindow()
    if (!win || win.isDestroyed()) createSpriteWindow()
    else showMainWindow()
  })
})

// Seed Chromium's spellchecker with the system locale (falling back to en-US).
// On macOS Electron uses the native spellchecker which ignores this list, but
// on Windows Chromium downloads Hunspell dictionaries on demand and
// won't enable any without an explicit language.
function configureSpellChecker() {
  try {
    const defaultSession = session.defaultSession

    if (!defaultSession || typeof defaultSession.setSpellCheckerLanguages !== 'function') {
      return
    }

    const available = defaultSession.availableSpellCheckerLanguages || []
    const locale = (app.getLocale && app.getLocale()) || 'en-US'
    const candidates = [locale, locale.split('-')[0], 'en-US', 'en']
    const chosen = candidates.find(lang => available.includes(lang)) || 'en-US'

    defaultSession.setSpellCheckerLanguages([chosen])
  } catch (error) {
    rememberLog(`Spellchecker setup failed: ${error.message}`)
  }
}

app.on('before-quit', () => {
  // Flip `isQuitting` first so the close interceptor (installed by
  // `tray.cjs::installCloseInterceptor`) lets the window actually destroy
  // itself instead of hiding. This is the universal quit hook — Cmd+Q on
  // macOS, the tray "Quit" item, and any other teardown path all funnel
  // through here, so the flag belongs in this single place.
  bridgeDeps.isQuitting = true
  destroyTray()

  // Tear down the Runner bridge so the Runner child doesn't outlive the
  // desktop shell. The bridge stop is async but the OS will reap the
  // process group on actual exit; this kicks off the orderly shutdown.
  if (bridgeDeps.runnerBridge) {
    try {
      bridgeDeps.runnerBridge.stop({ reason: 'app-quit' })
    } catch (error) {
      rememberLog(`[runner-bridge] quit cleanup failed: ${error?.message || error}`)
    }
  }

  if (desktopLogFlushTimer) {
    clearTimeout(desktopLogFlushTimer)
    desktopLogFlushTimer = null
  }
  flushDesktopLogBufferSync()
})

app.on('window-all-closed', () => {
  // Tray-resident app: when the close button is intercepted, the window
  // is hidden (not destroyed), so this event normally doesn't fire. It
  // only triggers after a real quit (tray "Quit" → app.quit → before-quit
  // flips isQuitting → windows destroy → this fires). macOS keeps the
  // event alive but bails because the dock-hide decision means there's no
  // path back to a window without a relaunch anyway.
  if (bridgeDeps.isQuitting && !IS_MAC) app.quit()
})
