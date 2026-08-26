import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

import { type DesktopBootProgress, IPC, type SpiritAgentConnection } from '@ipc/contracts'
import {
  app,
  BrowserWindow,
  clipboard,
  dialog,
  net as electronNet,
  ipcMain,
  Menu,
  nativeImage,
  powerMonitor,
  protocol,
  safeStorage,
  screen,
  session,
  shell,
  Tray
} from 'electron'
import log from 'electron-log/main'
import type { UpdateInfo } from 'electron-updater'

import type { BackendSession, SessionSnapshot } from './backend/session'
import { createBackendSession } from './backend/session'
import { registerAuthIpc } from './ipc/auth'
import { registerClipboardIpc } from './ipc/clipboard'
import { registerConnectionIpc } from './ipc/connection'
import { registerFilesIpc } from './ipc/files'
import { registerLogIpc } from './ipc/log'
import { createEnginePrefsCache, registerMediaIpc } from './ipc/media'
import { createModelDiskCache } from './ipc/model-disk-cache'
import { registerOnboardingAudioIpc } from './ipc/onboarding-audio'
import { autoStartBridge, autoStopBridge, registerRunnerIpc } from './ipc/runner'
import { registerRunnerConfigIpc } from './ipc/runner-config'
import { registerSkillsIpc } from './ipc/skills'
import { readRestPosition, registerSpriteIpc } from './ipc/sprite'
import { registerSystemIpc } from './ipc/system'
import { registerTitlebarIpc } from './ipc/titlebar'
import { registerUpdateIpc } from './ipc/update'
import { createDesktopLogger } from './lifecycle/desktop-log'
import { detectRemoteDisplay } from './lifecycle/platform'
import {
  destroyTray,
  installCloseInterceptor,
  installTray,
  rebuildTrayMenu,
  registerSingleInstanceForwarder,
  showMainWindow
} from './lifecycle/tray'
import type { RunnerBridge } from './runner/bridge'
import { createRunnerBridge } from './runner/bridge'
import { createRunnerProcess } from './runner/process'
import { createReverseRpc } from './runner/reverse-rpc'
import { createRunnerWsServer } from './runner/rpc-ws'
import { RunnerUpdater } from './runner/updater'
import {
  DATA_URL_READ_MAX_BYTES,
  DEFAULT_CSP_POLICY,
  DEFAULT_FETCH_TIMEOUT_MS,
  DEV_CSP_POLICY,
  resolvePathTimeoutMs,
  resolveReadableFileForIpc,
  resolveTimeoutMs
} from './security/hardening'
import { spiritagentHome } from './security/paths'
import { buildClientContext } from './shared/client-context'
import { readStoredBackendUrl, resolveNormalizedBackendUrl } from './shared/config'
import * as runnerConfigStore from './shared/lib/runner-config-store'
import { extensionForMimeType, mimeTypeForPath, STREAMABLE_MEDIA_EXTS } from './shared/mime'
import { atomicWriteFile, directoryExists, fileExists, sendToMain, sleep } from './shared/utils'

const USER_DATA_OVERRIDE = process.env.SPIRITAGENT_DESKTOP_USER_DATA_DIR

const DEV_SERVER = process.env.SPIRITAGENT_DESKTOP_DEV_SERVER
const IS_PACKAGED = app.isPackaged
const IS_MAC = process.platform === 'darwin'
const APP_ROOT = app.getAppPath()

if (process.env.SPIRITAGENT_DESKTOP_DISABLE_SINGLE_INSTANCE_LOCK !== '1') {
  if (!app.requestSingleInstanceLock()) {
    app.exit(0)
  }
}

const REMOTE_DISPLAY_REASON = detectRemoteDisplay()

if (REMOTE_DISPLAY_REASON) {
  app.disableHardwareAcceleration()
  app.commandLine.appendSwitch('disable-gpu-compositing')
  console.log(
    `[spiritagent] remote display detected (${REMOTE_DISPLAY_REASON}); disabling GPU hardware acceleration to prevent flicker`
  )
}

app.commandLine.appendSwitch('disable-renderer-backgrounding')
app.commandLine.appendSwitch('disable-backgrounding-occluded-windows')
app.commandLine.appendSwitch('disable-background-timer-throttling')

function resolveSpiritAgentHome(): string {
  if (USER_DATA_OVERRIDE) {
    return path.join(path.resolve(USER_DATA_OVERRIDE), 'spiritagent-home')
  }

  return spiritagentHome()
}

const SPIRITAGENT_HOME = resolveSpiritAgentHome()
fs.mkdirSync(SPIRITAGENT_HOME, { recursive: true })
app.setPath('userData', SPIRITAGENT_HOME)

runnerConfigStore.init({ spiritagentHome: SPIRITAGENT_HOME })

const APP_NAME = 'SpiritAgent'
const TITLEBAR_HEIGHT = 34
const MACOS_TRAFFIC_LIGHTS_HEIGHT = 14

const WINDOW_BUTTON_POSITION = {
  x: 24,
  y: TITLEBAR_HEIGHT / 2 - MACOS_TRAFFIC_LIGHTS_HEIGHT / 2
}

const NATIVE_OVERLAY_BUTTON_WIDTH = 144

const APP_ICON_PATHS = [
  path.join(APP_ROOT, 'assets', 'icon.png'),
  path.join(process.resourcesPath, 'icon.ico'),
  path.join(unpackedPathFor(APP_ROOT), 'icon.ico')
]

let rendererTitleBarTheme: null | { background: string; foreground: string } = null

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

  // 工具窗口始终渲染钉死的深色调色板——覆盖条必须与系统外观无关地
  // 与深色标题栏一致，否则浅色条会落在深色标题栏上。
  return {
    color: '#0d0d0d',
    height: TITLEBAR_HEIGHT,
    symbolColor: '#f2f2f2'
  }
}

app.setName(APP_NAME)

if (process.platform === 'win32') {
  app.setAppUserModelId('io.spiritagent.agent')
}

app.setAboutPanelOptions({
  applicationName: APP_NAME,
  applicationVersion: resolveSpiritAgentVersion(),
  copyright: 'Copyright © 2026 SpiritAgent'
})

const MEDIA_PROTOCOL = 'spiritagent-media'

protocol.registerSchemesAsPrivileged([
  {
    privileges: {
      secure: true,
      standard: true,
      stream: true,
      supportFetchAPI: true
    },
    scheme: MEDIA_PROTOCOL
  }
])

function registerMediaProtocol() {
  protocol.handle(MEDIA_PROTOCOL, async request => {
    let resolvedPath: string

    try {
      const url = new URL(request.url)
      const rawPath = decodeURIComponent(url.pathname)

      const filePath = process.platform === 'win32' ? rawPath.replace(/^\/+([A-Za-z]:)/, '$1') : rawPath

      ;({ resolvedPath } = await resolveReadableFileForIpc(filePath, { purpose: 'Media stream' }))
    } catch {
      return new Response('Media not found', { status: 404 })
    }

    if (!STREAMABLE_MEDIA_EXTS.has(path.extname(resolvedPath).toLowerCase())) {
      return new Response('Unsupported media type', { status: 415 })
    }

    return electronNet.fetch(pathToFileURL(resolvedPath).toString(), {
      bypassCustomProtocolHandlers: true,
      headers: request.headers
    })
  })
}

let mainWindow: BrowserWindow | null = null
let toolWindow: BrowserWindow | null = null
let spriteBoundsListenerInstalled = false
const RENDERER_RELOAD_WINDOW_MS = 60_000
const RENDERER_RELOAD_MAX = 3
let rendererReloadTimes: number[] = []

const desktopLogger = createDesktopLogger({
  spiritagentHome: SPIRITAGENT_HOME,
  isPackaged: IS_PACKAGED
})

const rememberLog = (chunk: unknown): void => desktopLogger.rememberLog(chunk)

let bootProgressState: DesktopBootProgress = {
  error: null,
  message: 'Waiting to start SpiritAgent backend',
  phase: 'idle',
  progress: 0,
  running: false,
  timestamp: Date.now()
}

function openExternalUrl(rawUrl: string): boolean {
  const raw = String(rawUrl || '').trim()

  if (!raw) {
    return false
  }

  let parsed: URL

  try {
    parsed = new URL(raw)
  } catch {
    return false
  }

  if (parsed.protocol === 'file:') {
    let localPath: string

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
          const msg = revealError instanceof Error ? revealError.message : String(revealError)
          rememberLog(`[file] showItemInFolder failed: ${msg}`)
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

function clampBootProgress(value: unknown): number {
  const numeric = Number(value)

  if (!Number.isFinite(numeric)) {
    return 0
  }

  return Math.max(0, Math.min(100, Math.round(numeric)))
}

function broadcastBootProgress(): void {
  sendToMain(mainWindow, IPC.event.bootProgress, bootProgressState)
}

function updateBootProgress(update: Partial<DesktopBootProgress>, options: { allowDecrease?: boolean } = {}): void {
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

async function advanceBootProgress(phase: string, message: string, progress: number): Promise<void> {
  updateBootProgress({
    error: null,
    message,
    phase,
    progress,
    running: true
  })
}

function unpackedPathFor(filePath: string): string {
  return filePath.replace(/app\.asar(?=$|[\\/])/, 'app.asar.unpacked')
}

function resolveWebDist(): string {
  const override = process.env.SPIRITAGENT_DESKTOP_WEB_DIST

  if (override && directoryExists(path.resolve(override))) {
    return path.resolve(override)
  }

  const unpackedDist = path.join(unpackedPathFor(APP_ROOT), 'dist')

  if (directoryExists(unpackedDist)) {
    return unpackedDist
  }

  const fallback = path.join(APP_ROOT, 'dist')

  if (IS_PACKAGED && /app\.asar(?=$|[\\/])/.test(fallback) && !directoryExists(fallback)) {
    rememberLog(
      `[web-dist] dashboard frontend dir resolved to an asar-internal path that ` +
        `is not a real directory: ${fallback}. Static routes will 404. ` +
        'Ensure dist/** is unpacked (asarUnpack) or set SPIRITAGENT_DESKTOP_WEB_DIST.'
    )
  }

  return fallback
}

function htmlFileNameForRole(role?: string): string {
  if (role === 'sprite') {
    return 'sprite.html'
  }

  if (role === 'tool' || role === 'hub') {
    return 'hub.html'
  }

  if (role === 'clip' || role === 'anim' || role === 'animation') {
    return 'clip-debugger.html'
  }

  return 'index.html'
}

function resolveRendererHtml(htmlFileName = 'index.html'): string {
  const candidates = [path.join(APP_ROOT, 'dist', htmlFileName), path.join(resolveWebDist(), htmlFileName)]
  const found = candidates.find(fileExists)

  if (found) {
    return found
  }

  if (htmlFileName !== 'index.html') {
    const fallbackCandidates = [path.join(APP_ROOT, 'dist', 'index.html'), path.join(resolveWebDist(), 'index.html')]
    const fallbackFound = fallbackCandidates.find(fileExists)

    if (fallbackFound) {
      return fallbackFound
    }
  }

  rememberLog(
    `[renderer] ${htmlFileName} not found — the desktop app was packaged without a ` +
      'renderer bundle. Tried: ' +
      candidates.join(', ') +
      '. Rebuild via the Tauri SpiritAgent-Setup installer.'
  )

  return candidates[0]
}

function resolveSpiritAgentVersion(): string {
  return app.getVersion()
}

async function fetchJson(
  url: string,
  token?: string,
  options: { body?: unknown; method?: string; timeoutMs?: number } = {}
): Promise<unknown> {
  const timeoutMs = resolveTimeoutMs(options.timeoutMs, DEFAULT_FETCH_TIMEOUT_MS)
  const body = options.body !== undefined ? JSON.stringify(options.body) : undefined

  const headers: Record<string, string> = {
    ...(body ? { 'Content-Type': 'application/json' } : {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {})
  }

  const res = await electronNet.fetch(url, {
    body,
    headers,
    method: options.method || 'GET',
    signal: AbortSignal.timeout(timeoutMs)
  })

  const text = await res.text().catch(() => '')

  if (!res.ok) {
    let pathname = url

    try {
      pathname = new URL(url).pathname
    } catch {
      /* ignore invalid url formatting in error */
    }

    throw new Error(`${res.status} ${pathname}: ${text || res.statusText}`)
  }

  if (!text) {
    return null
  }

  const looksHtml = /^\s*<(?:!doctype|html)/i.test(text)
  const contentType = String(res.headers.get('content-type') || '')

  if (looksHtml || contentType.includes('text/html')) {
    throw new Error(
      `Expected JSON from ${url} but got HTML (status ${res.status}). The endpoint is likely missing on the SpiritAgent backend.`
    )
  }

  try {
    return JSON.parse(text)
  } catch {
    throw new Error(`Invalid JSON from ${url} (status ${res.status}): ${text.slice(0, 200)}`)
  }
}

function filenameFromUrl(rawUrl: string, fallback = 'image'): string {
  try {
    const parsed = new URL(rawUrl)
    const base = path.basename(decodeURIComponent(parsed.pathname || ''))

    return base && base.includes('.') ? base : fallback
  } catch {
    return fallback
  }
}

async function resourceBufferFromUrl(rawUrl: string): Promise<{ buffer: Buffer; mimeType: string }> {
  if (!rawUrl) {
    throw new Error('Missing URL')
  }

  if (rawUrl.startsWith('data:')) {
    const match = rawUrl.match(/^data:([^;,]+)?(;base64)?,(.*)$/s)

    if (!match) {
      throw new Error('Invalid data URL')
    }

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

  const res = await electronNet.fetch(rawUrl)

  if (!res.ok) {
    throw new Error(`Failed to fetch ${rawUrl}: ${res.status}`)
  }

  const arrayBuf = await res.arrayBuffer()

  return {
    buffer: Buffer.from(arrayBuf),
    mimeType: res.headers.get('content-type') || 'application/octet-stream'
  }
}

async function copyImageFromUrl(rawUrl: string): Promise<void> {
  const { buffer } = await resourceBufferFromUrl(rawUrl)
  const image = nativeImage.createFromBuffer(buffer)

  if (image.isEmpty()) {
    throw new Error('Could not read image')
  }

  clipboard.writeImage(image)
}

async function saveImageFromUrl(rawUrl: string): Promise<boolean> {
  const { buffer, mimeType } = await resourceBufferFromUrl(rawUrl)
  const fallbackName = filenameFromUrl(rawUrl, `image${extensionForMimeType(mimeType) || '.png'}`)

  const result = await dialog.showSaveDialog(mainWindow!, {
    defaultPath: fallbackName,
    title: 'Save Image'
  })

  if (result.canceled || !result.filePath) {
    return false
  }

  await fs.promises.writeFile(result.filePath, buffer)

  return true
}

async function writeComposerImage(buffer: Buffer, ext = '.png'): Promise<string> {
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

async function waitForSpiritAgent(baseUrl: string, token?: string): Promise<void> {
  const deadline = Date.now() + 45_000
  let lastError: unknown = null

  while (Date.now() < deadline) {
    try {
      await fetchJson(`${baseUrl}/health`, token)

      return
    } catch (error) {
      lastError = error
      await sleep(500)
    }
  }

  const lastErrorMsg = lastError instanceof Error ? lastError.message : String(lastError ?? '')
  throw new Error(`SpiritAgent backend did not become ready: ${lastErrorMsg || 'timeout'}`)
}

function getWindowButtonPosition(): { x: number; y: number } | null {
  if (!IS_MAC) {
    return null
  }

  return mainWindow?.getWindowButtonPosition?.() || WINDOW_BUTTON_POSITION
}

function getNativeOverlayWidth(): number {
  return IS_MAC ? 0 : NATIVE_OVERLAY_BUTTON_WIDTH
}

function getWindowState() {
  return {
    isFullscreen: Boolean(mainWindow?.isFullScreen?.()),
    nativeOverlayWidth: getNativeOverlayWidth(),
    windowButtonPosition: getWindowButtonPosition()
  }
}

function sameWindowButtonPosition(
  a: null | undefined | { x: number; y: number },
  b: null | undefined | { x: number; y: number }
): boolean {
  return !!a && !!b && a.x === b.x && a.y === b.y
}

function sendPowerResume(): void {
  sendToMain(mainWindow, IPC.event.powerResume)
}

let powerResumeRegistered = false

function registerPowerResumeListeners(): void {
  if (powerResumeRegistered) {
    return
  }

  powerResumeRegistered = true

  try {
    powerMonitor.on('resume', sendPowerResume)
    powerMonitor.on('unlock-screen', sendPowerResume)
  } catch {
    // 尽力而为
  }
}

function getAppIconPath(): null | string {
  return APP_ICON_PATHS.find(fileExists) || null
}

function buildApplicationMenu(): Menu {
  const template: Electron.MenuItemConstructorOptions[] = []

  if (IS_MAC) {
    template.push({
      label: APP_NAME,
      submenu: [
        { click: () => showAboutPanelFresh(), label: `About ${APP_NAME}` },
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
              mainWindow?.close()
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
        accelerator: 'CommandOrControl+0',
        click: () => {
          setAndPersistZoomLevel(mainWindow, 0)
        },
        label: 'Actual Size'
      },
      {
        accelerator: 'CommandOrControl+Plus',
        click: () => {
          if (mainWindow && !mainWindow.isDestroyed()) {
            setAndPersistZoomLevel(mainWindow, mainWindow.webContents.getZoomLevel() + 0.1)
          }
        },
        label: 'Zoom In'
      },
      {
        accelerator: 'CommandOrControl+-',
        click: () => {
          if (mainWindow && !mainWindow.isDestroyed()) {
            setAndPersistZoomLevel(mainWindow, mainWindow.webContents.getZoomLevel() - 0.1)
          }
        },
        label: 'Zoom Out'
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

function toggleDevTools(targetWin: BrowserWindow): void {
  const { webContents } = targetWin

  if (webContents.isDevToolsOpened()) {
    webContents.closeDevTools()
  } else {
    webContents.openDevTools({ mode: 'detach' })
  }
}

function installDevToolsShortcut(targetWin: BrowserWindow): void {
  targetWin.webContents.on('before-input-event', (event, input) => {
    const key = input.key.toLowerCase()

    const isInspectShortcut =
      input.key === 'F12' ||
      (IS_MAC && input.meta && input.alt && key === 'i') ||
      (!IS_MAC && input.control && input.shift && key === 'i')

    if (!isInspectShortcut) {
      return
    }

    event.preventDefault()
    toggleDevTools(targetWin)
  })
}

const ZOOM_FILE = 'desktop-zoom.json'

function clampZoomLevel(value: number): number {
  if (!Number.isFinite(value)) {
    return 0
  }

  return Math.min(Math.max(value, -9), 9)
}

function readPersistedZoomLevel(): number | null {
  try {
    const filePath = path.join(app.getPath('userData'), ZOOM_FILE)
    const raw = fs.readFileSync(filePath, 'utf8')
    const parsed = JSON.parse(raw) as { zoomLevel?: unknown }

    if (parsed && typeof parsed.zoomLevel === 'number') {
      return clampZoomLevel(parsed.zoomLevel)
    }
  } catch {
    // 尚未保存或文件损坏
  }

  return null
}

function writePersistedZoomLevel(zoomLevel: number): void {
  try {
    const filePath = path.join(app.getPath('userData'), ZOOM_FILE)
    fs.writeFileSync(filePath, JSON.stringify({ zoomLevel }), 'utf8')
  } catch (error: unknown) {
    const msg = error instanceof Error ? error.message : String(error)
    rememberLog(`[zoom] persist failed: ${msg}`)
  }
}

function setAndPersistZoomLevel(targetWin: BrowserWindow | null, zoomLevel: number): void {
  if (!targetWin || targetWin.isDestroyed()) {
    return
  }

  const next = clampZoomLevel(zoomLevel)
  targetWin.webContents.setZoomLevel(next)
  writePersistedZoomLevel(next)
}

function restorePersistedZoomLevel(targetWin: BrowserWindow | null): void {
  if (!targetWin || targetWin.isDestroyed()) {
    return
  }

  const stored = readPersistedZoomLevel()

  if (stored !== null) {
    targetWin.webContents.setZoomLevel(stored)
  }
}

function installZoomShortcuts(targetWin: BrowserWindow): void {
  const ZOOM_STEP = 0.1
  targetWin.webContents.on('before-input-event', (event, input) => {
    const mod = IS_MAC ? input.meta : input.control

    if (!mod || input.alt || input.shift) {
      return
    }

    const key = input.key

    if (key === '0') {
      event.preventDefault()
      setAndPersistZoomLevel(targetWin, 0)
    } else if (key === '=' || key === '+') {
      event.preventDefault()
      setAndPersistZoomLevel(targetWin, targetWin.webContents.getZoomLevel() + ZOOM_STEP)
    } else if (key === '-') {
      event.preventDefault()
      setAndPersistZoomLevel(targetWin, targetWin.webContents.getZoomLevel() - ZOOM_STEP)
    }
  })
}

function installContextMenu(targetWin: BrowserWindow): void {
  targetWin.webContents.on('context-menu', (_event, params) => {
    const template: Electron.MenuItemConstructorOptions[] = []
    const hasSelection = Boolean(params.selectionText?.trim())
    const hasImage = params.mediaType === 'image' && Boolean(params.srcURL)
    const hasLink = Boolean(params.linkURL)
    const isEditable = Boolean(params.isEditable)

    if (hasImage) {
      template.push(
        {
          enabled: !params.srcURL.startsWith('data:'),
          label: 'Open Image',
          click: () => {
            if (params.srcURL && !params.srcURL.startsWith('data:')) {
              openExternalUrl(params.srcURL)
            }
          }
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
      if (template.length) {
        template.push({ type: 'separator' })
      }

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

    const suggestions = Array.isArray(params.dictionarySuggestions) ? params.dictionarySuggestions : []

    if (isEditable && params.misspelledWord && suggestions.length > 0) {
      if (template.length) {
        template.push({ type: 'separator' })
      }

      for (const suggestion of suggestions.slice(0, 5)) {
        template.push({
          label: suggestion,
          click: () => targetWin.webContents.replaceMisspelling(suggestion)
        })
      }

      template.push({ type: 'separator' })
      template.push({
        label: 'Add to dictionary',
        click: () => targetWin.webContents.session.addWordToSpellCheckerDictionary(params.misspelledWord)
      })
    }

    if (hasSelection || isEditable) {
      if (template.length) {
        template.push({ type: 'separator' })
      }

      if (isEditable) {
        template.push(
          { enabled: params.editFlags.canCut, role: 'cut' },
          { enabled: params.editFlags.canCopy, role: 'copy' },
          { enabled: params.editFlags.canPaste, role: 'paste' },
          { type: 'separator' },
          { enabled: params.editFlags.canSelectAll, role: 'selectAll' }
        )
      } else {
        template.push({ enabled: params.editFlags.canCopy, role: 'copy' })
      }
    }

    if (!template.length) {
      template.push({ role: 'selectAll' })
    }

    Menu.buildFromTemplate(template).popup({ window: targetWin })
  })
}

function isAudioCapturePermission(
  permission: string,
  details:
    | Electron.PermissionRequest
    | Electron.FilesystemPermissionRequest
    | Electron.MediaAccessPermissionRequest
    | Electron.OpenExternalPermissionRequest
): boolean {
  if (permission === 'audioCapture') {
    return true
  }

  if (permission !== 'media') {
    return false
  }

  const mediaTypes =
    'mediaTypes' in details ? (details as { mediaTypes?: ReadonlyArray<string> }).mediaTypes : undefined

  if (!Array.isArray(mediaTypes) || mediaTypes.length === 0) {
    return true
  }

  return mediaTypes.includes('audio') && !mediaTypes.includes('video')
}

function installMediaPermissions(): void {
  session.defaultSession.setPermissionRequestHandler((_webContents, permission, callback, details) => {
    callback(isAudioCapturePermission(permission, details))
  })

  session.defaultSession.setPermissionCheckHandler((_webContents, permission, _origin, details) => {
    if ((permission as string) === 'media' || (permission as string) === 'audioCapture') {
      const mediaType = details?.mediaType

      if (mediaType === 'video') {
        return false
      }

      return true
    }

    return false
  })
}

function installContentSecurityPolicy(): void {
  // dev 用放宽的 CSP（允许 inline + eval），让 Vite 的 React Fast Refresh
  // preamble 与 HMR 跑得起来；生产仍锁 DEFAULT_CSP_POLICY。
  const policy = IS_PACKAGED ? DEFAULT_CSP_POLICY : DEV_CSP_POLICY
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [policy]
      }
    })
  })
}

const UPDATE_INITIAL_CHECK_DELAY_MS = 30_000

let runnerUpdaterSingleton: RunnerUpdater | null = null

function getRunnerUpdater(): RunnerUpdater {
  if (runnerUpdaterSingleton) {
    return runnerUpdaterSingleton
  }

  runnerUpdaterSingleton = new RunnerUpdater({
    bridgeDeps,
    fetchImpl: electronNet.fetch as unknown as typeof globalThis.fetch
  })

  return runnerUpdaterSingleton
}

function setupAutoUpdater(): void {
  if (!app.isPackaged) {
    return
  }

  const { autoUpdater } = require('electron-updater')

  autoUpdater.autoDownload = false
  autoUpdater.autoInstallOnAppQuit = false
  autoUpdater.logger = log

  const baseUrl = resolveNormalizedBackendUrl(SPIRITAGENT_HOME)

  if (!baseUrl) {
    log.info('no backend URL configured; auto-updater disabled until activation')

    return
  }

  const updateBaseUrl = baseUrl + '/api/update'
  const publicKeyPath = getBundledPublicKeyPath()

  if (!publicKeyPath) {
    log.warn('update.pub not found in extraResources; runner signature verification will fail')
  }

  autoUpdater.setFeedURL({
    provider: 'generic',
    url: updateBaseUrl
  })

  autoUpdater.on('update-downloaded', (info: UpdateInfo) => {
    log.info('desktop update downloaded; starting runner prefetch', info?.version)
    getRunnerUpdater()
      .prefetchRunnerAssets({
        publicKeyPath,
        updateBaseUrl,
        version: info?.version || app.getVersion()
      })
      .catch(err => {
        log.warn('runner prefetch failed:', err?.message || err)
      })
  })

  const timer = setTimeout(() => {
    autoUpdater.checkForUpdates().catch((error: unknown) => {
      const msg = error instanceof Error ? error.message : String(error)
      log.warn('initial update check failed:', msg)
    })
  }, UPDATE_INITIAL_CHECK_DELAY_MS)

  if (typeof timer.unref === 'function') {
    timer.unref()
  }
}

function getBundledPublicKeyPath(): null | string {
  try {
    const candidates = [
      path.join(process.resourcesPath || '', 'update.pub'),
      path.join(APP_ROOT, 'update.pub'),
      path.join(import.meta.dirname, '..', 'update.pub'),
      path.join(import.meta.dirname, 'update.pub'),
      path.join(APP_ROOT, '..', 'scripts', 'release-keys', 'update.pub'),
      path.resolve(APP_ROOT, '../../scripts/release-keys/update.pub')
    ]

    return candidates.find(candidate => fs.existsSync(candidate)) || null
  } catch {
    return null
  }
}

async function resolveRemoteBackend(): Promise<null | { baseUrl: string }> {
  const url = resolveNormalizedBackendUrl(SPIRITAGENT_HOME)

  return url ? { baseUrl: url } : null
}

let getAuthToken = (): string | null => null
let cachedBackend: SpiritAgentConnection | null = null
let pendingBackend: Promise<SpiritAgentConnection> | null = null

function resetBackendCache(): void {
  cachedBackend = null
  pendingBackend = null
}

async function mintWsTicket(baseUrl: string, token: string | null): Promise<string | null> {
  if (!token) {
    return null
  }

  try {
    const res = (await fetchJson(`${baseUrl}/api/user/ws-ticket`, token, { method: 'POST', timeoutMs: 5000 })) as {
      access_token?: string
    }

    return res?.access_token || null
  } catch (error: unknown) {
    const msg = error instanceof Error ? error.message : String(error)
    rememberLog(`[ws-ticket] mint failed: ${msg}`)

    return null
  }
}

async function ensureBackend(): Promise<SpiritAgentConnection> {
  if (cachedBackend) {
    const token = getAuthToken()
    const tokenChanged = token !== cachedBackend.token

    if (
      !tokenChanged &&
      cachedBackend.isFullscreen === Boolean(mainWindow?.isFullScreen?.()) &&
      cachedBackend.nativeOverlayWidth === getNativeOverlayWidth() &&
      sameWindowButtonPosition(getWindowButtonPosition(), cachedBackend.windowButtonPosition)
    ) {
      return cachedBackend
    }
  }

  // 并发调用共享一条 in-flight Promise,避免重复跑 boot phase
  if (pendingBackend) {
    return pendingBackend
  }

  pendingBackend = (async () => {
    try {
      if (cachedBackend) {
        const liveWindowState = getWindowState()
        const wsBase = cachedBackend.baseUrl.replace(/^http/, 'ws')
        const token = getAuthToken()
        const wsTicket = await mintWsTicket(cachedBackend.baseUrl, token)
        cachedBackend = {
          ...cachedBackend,
          ...liveWindowState,
          token,
          wsUrl: wsTicket ? `${wsBase}/api/chat/ws?ticket=${wsTicket}` : `${wsBase}/api/chat/ws`
        }

        return cachedBackend
      }

      await advanceBootProgress('backend.resolve', 'Resolving SpiritAgent backend', 8)
      const remote = await resolveRemoteBackend()

      if (!remote) {
        throw new Error('No remote SpiritAgent backend configured.')
      }

      const token = getAuthToken()
      await advanceBootProgress('backend.remote', `Connecting to remote SpiritAgent backend at ${remote.baseUrl}`, 24)
      await waitForSpiritAgent(remote.baseUrl, token || undefined)
      updateBootProgress({
        error: null,
        message: 'Remote SpiritAgent backend is ready',
        phase: 'backend.ready',
        progress: 94,
        running: true
      })
      const wsBase = remote.baseUrl.replace(/^http/, 'ws')
      const wsTicket = await mintWsTicket(remote.baseUrl, token)
      cachedBackend = {
        baseUrl: remote.baseUrl,
        token,
        wsUrl: wsTicket ? `${wsBase}/api/chat/ws?ticket=${wsTicket}` : `${wsBase}/api/chat/ws`,
        ...getWindowState()
      }

      return cachedBackend
    } finally {
      pendingBackend = null
    }
  })()

  return pendingBackend
}

function rendererUrlFor(role: string): string {
  const htmlFile = htmlFileNameForRole(role)

  if (DEV_SERVER) {
    return `${DEV_SERVER}/${htmlFile}`
  }

  return pathToFileURL(resolveRendererHtml(htmlFile)).toString()
}

function installStandardWindowHandlers(win: BrowserWindow): void {
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
        if (!win || win.isDestroyed()) {
          return
        }

        try {
          win.webContents.reload()
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err)
          rememberLog(`[renderer] reload after crash failed: ${msg}`)
        }
      })
    }
  })
  win.webContents.on('unresponsive', () => rememberLog('[renderer] webContents became unresponsive'))

  win.webContents.on(
    'console-message',
    (
      _event: Electron.Event,
      detailsOrLevel: number | Electron.WebContentsConsoleMessageEventParams,
      message?: string,
      line?: number,
      sourceId?: string
    ) => {
      const details = detailsOrLevel && typeof detailsOrLevel === 'object' ? detailsOrLevel : null

      const level: number = details
        ? details.level === 'error'
          ? 3
          : details.level === 'warning'
            ? 2
            : details.level === 'info'
              ? 1
              : 0
        : (detailsOrLevel as number)

      if (level !== 3) {
        return
      }

      const text = details ? details.message : (message ?? '')
      const src = details ? details.sourceId : (sourceId ?? '')
      const lineNo = details ? details.lineNumber : (line ?? 0)
      rememberLog(`[renderer console] ${text} (${src}:${lineNo})`)
    }
  )
  installCloseInterceptor(win)
}

function createToolWindow(): void {
  const icon = getAppIconPath() || undefined
  toolWindow = new BrowserWindow({
    backgroundColor: '#0d0d0d',
    height: 800,
    icon,
    minHeight: 620,
    minWidth: 400,
    title: 'SpiritAgent',
    titleBarOverlay: getTitleBarOverlayOptions(),
    titleBarStyle: 'hidden',
    trafficLightPosition: IS_MAC ? WINDOW_BUTTON_POSITION : undefined,
    vibrancy: IS_MAC ? 'sidebar' : undefined,
    webPreferences: {
      backgroundThrottling: false,
      contextIsolation: true,
      devTools: !app.isPackaged,
      nodeIntegration: false,
      preload: path.join(import.meta.dirname, 'preload.cjs'),
      sandbox: true
    },
    width: 1220
  })

  if (IS_MAC) {
    toolWindow.setWindowButtonPosition?.(WINDOW_BUTTON_POSITION)
  }

  installZoomShortcuts(toolWindow)
  installContextMenu(toolWindow)
  installStandardWindowHandlers(toolWindow)

  void toolWindow.loadURL(rendererUrlFor('tool'))

  toolWindow.webContents.once('did-finish-load', () => {
    restorePersistedZoomLevel(toolWindow)
  })
}

const SPRITE_TRANSPARENT = !REMOTE_DISPLAY_REASON

function applySpriteBounds(preferredOrigin?: { x: number; y: number }): void {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return
  }

  // 贴住窗口当前覆盖的那块显示器（或启动时包含 preferredOrigin 的那块），
  // 而不是每次都弹回主显示器——精灵必须停留在用户拖到的那块显示器上。
  // 当原显示器被拔掉时，getDisplayMatching 会回退到最近的那块。
  const base = preferredOrigin
    ? { height: 1, width: 1, x: preferredOrigin.x, y: preferredOrigin.y }
    : mainWindow.getBounds()

  mainWindow.setBounds(screen.getDisplayMatching(base).workArea)
}

function createSpriteWindow(): void {
  const icon = getAppIconPath() || undefined
  mainWindow = new BrowserWindow({
    alwaysOnTop: true,
    backgroundColor: '#00000000',
    frame: false,
    hasShadow: false,
    height: 320,
    movable: false,
    resizable: false,
    show: false,
    skipTaskbar: true,
    // `type: 'panel'` 仅适用于 macOS（Cocoa NSPanel）；在 Win/Linux 上设置会输出 deprecation 警告。
    type: IS_MAC ? 'panel' : undefined,
    transparent: SPRITE_TRANSPARENT,
    webPreferences: {
      backgroundThrottling: false,
      contextIsolation: true,
      devTools: !app.isPackaged,
      nodeIntegration: false,
      preload: path.join(import.meta.dirname, 'preload.cjs'),
      sandbox: true
    },
    width: 480
  })

  applySpriteBounds(readRestPosition(app.getPath('userData'))?.origin)
  mainWindow.setIgnoreMouseEvents(true, { forward: SPRITE_TRANSPARENT })

  // macOS 用 'screen-saver' z-band（位于 floating 之上，能压过 exclusive fullscreen 游戏）；
  // Win/Linux 回退 'floating'。Windows 的 exclusive fullscreen 完全绕过 DWM，
  // 伙伴窗口无法覆盖在上面（已记录的限制）。
  if (IS_MAC) {
    mainWindow.setAlwaysOnTop(true, 'screen-saver', 1)
  } else {
    mainWindow.setAlwaysOnTop(true, 'floating')
  }

  if (IS_MAC && icon) {
    app.dock?.setIcon(icon)
  }

  if (!spriteBoundsListenerInstalled) {
    spriteBoundsListenerInstalled = true
    screen.on('display-metrics-changed', () => applySpriteBounds())
  }

  installStandardWindowHandlers(mainWindow)

  void mainWindow.loadURL(rendererUrlFor('sprite'))
  mainWindow.webContents.once('did-finish-load', () => {
    broadcastBootProgress()
    mainWindow?.showInactive()
  })
}

function showToolWindow(): void {
  if (!toolWindow || toolWindow.isDestroyed()) {
    createToolWindow()

    return
  }

  if (toolWindow.isMinimized()) {
    toolWindow.restore()
  }

  if (!toolWindow.isVisible()) {
    if (process.platform === 'win32') {
      toolWindow.setSkipTaskbar(false)
    }

    toolWindow.show()
  }

  toolWindow.focus()
}

function hideToolWindow(): void {
  if (toolWindow && !toolWindow.isDestroyed()) {
    toolWindow.hide()

    if (process.platform === 'win32') {
      toolWindow.setSkipTaskbar(true)
    }
  }
}

function broadcastAuthChanged(snapshot: null | SessionSnapshot): void {
  rebuildTrayMenu()
  const authenticated = Boolean(snapshot?.hasToken)
  const payload = { authenticated, snapshot: authenticated ? snapshot : null }

  for (const win of [mainWindow, toolWindow]) {
    if (win && !win.isDestroyed()) {
      win.webContents.send(IPC.event.authChanged, payload)
    }
  }
}

registerSystemIpc({
  electron: { app },
  ipcMain
})
registerTitlebarIpc({
  getTitleBarOverlayOptions,
  getToolWindow: () => toolWindow,
  ipcMain,
  setRendererTitleBarTheme: theme => {
    rendererTitleBarTheme = theme
  }
})
registerClipboardIpc({
  electron: { clipboard },
  ipcMain,
  writeComposerImage
})
registerLogIpc({ ipcMain, log: chunk => rememberLog(chunk) })
registerFilesIpc({
  electron: { dialog, getMainWindow: () => mainWindow },
  hardening: { DATA_URL_READ_MAX_BYTES, resolveReadableFileForIpc },
  ipcMain,
  mimeTypeForPath
})
registerOnboardingAudioIpc({
  app,
  appRoot: APP_ROOT,
  spiritagentHome: SPIRITAGENT_HOME,
  hardening: { resolveReadableFileForIpc },
  ipcMain,
  mimeTypeForPath
})
const electronFetch = electronNet.fetch as unknown as typeof globalThis.fetch

const modelDiskCache = createModelDiskCache({
  defaultFetchFn: electronFetch,
  spiritagentHome: SPIRITAGENT_HOME
})

registerConnectionIpc({
  defaultFetchTimeoutMs: DEFAULT_FETCH_TIMEOUT_MS,
  ensureBackend,
  fetchImpl: electronFetch,
  fetchJson,
  getBootProgressState: () => bootProgressState,
  ipcMain,
  mintWsTicket,
  modelDiskCache,
  resolvePathTimeoutMs,
  resolveTimeoutMs,
  setCachedWsUrl: (wsUrl: string) => {
    if (cachedBackend) {
      cachedBackend = { ...cachedBackend, wsUrl }
    }
  }
})
registerMediaIpc({
  spiritagentHome: SPIRITAGENT_HOME,
  ensureBackend,
  fetchImpl: electronFetch,
  getEnginePrefs: createEnginePrefsCache({
    ensureBackend,
    fetchImpl: electronFetch
  }),
  getRunnerBridge: () => bridgeDeps.runnerBridge,
  ipcMain,
  log: chunk => rememberLog(chunk)
})

function showAboutPanelFresh(): void {
  app.setAboutPanelOptions({
    applicationName: APP_NAME,
    applicationVersion: app.getVersion(),
    copyright: 'Copyright © 2026 SpiritAgent'
  })
  app.showAboutPanel()
}

interface RunnerBridgeDeps {
  app: { getPath: (name: string) => string; [key: string]: unknown }
  atomicWriteFile: typeof atomicWriteFile
  autoStartBridge: () => void
  autoStopBridge: () => void
  backendSession: null | BackendSession
  broadcastAuthChanged: (snapshot: null | SessionSnapshot) => void
  buildClientContext: () => ReturnType<typeof buildClientContext>
  createBackendSession: typeof createBackendSession
  createReverseRpc: typeof createReverseRpc
  createRunnerBridge: typeof createRunnerBridge
  createRunnerProcess: typeof createRunnerProcess
  createRunnerWsServer: typeof createRunnerWsServer
  electronNet: typeof electronNet
  ensureBackendSession: () => BackendSession
  fetchJson: (
    url: string,
    token?: string,
    options?: { body?: unknown; method?: string; timeoutMs?: number }
  ) => Promise<unknown>
  fileExists: typeof fileExists
  getMainWindow: () => BrowserWindow | null
  getSpriteWindow: () => BrowserWindow | null
  getToolWindow: () => BrowserWindow | null
  hideToolWindow: () => void
  isQuitting: boolean
  rebuildTrayMenu: () => void
  rememberLog: (chunk: string) => void
  resetBackendCache: () => void
  resolveSpiritAgentVersion: () => string
  rewireAuthToken: () => void
  runnerBridge: null | RunnerBridge
  safeStorage: typeof safeStorage
  showToolWindow: () => void
  spiritagentHome: string
  taggedLogger: (prefix: string) => (chunk: string) => void
}

const bridgeDeps: RunnerBridgeDeps = {
  app: app as unknown as { getPath: (name: string) => string; [key: string]: unknown },
  atomicWriteFile,
  autoStartBridge: () => autoStartBridge(bridgeDeps),
  autoStopBridge: () => autoStopBridge(bridgeDeps),
  backendSession: null,
  broadcastAuthChanged,
  buildClientContext: () =>
    buildClientContext({
      spiritagentHome: SPIRITAGENT_HOME,
      desktopVersion: resolveSpiritAgentVersion()
    }),
  createBackendSession,
  createReverseRpc,
  createRunnerBridge,
  createRunnerProcess,
  createRunnerWsServer,
  spiritagentHome: SPIRITAGENT_HOME,
  electronNet,
  ensureBackendSession: () => {
    if (bridgeDeps.backendSession) {
      return bridgeDeps.backendSession
    }

    bridgeDeps.backendSession = createBackendSession({
      appVersion: resolveSpiritAgentVersion(),
      defaultBaseUrl: readStoredBackendUrl(SPIRITAGENT_HOME) || null,
      fetchImpl: (url: string, options?: RequestInit) => electronNet.fetch(url, options),
      log: (chunk: string) => rememberLog(chunk),
      safeStorage,
      userDataDir: app.getPath('userData')
    })

    try {
      bridgeDeps.backendSession
        .restoreSession()
        .then((snapshot: null | SessionSnapshot) => {
          if (snapshot) {
            broadcastAuthChanged(snapshot)
            autoStartBridge(bridgeDeps)
          } else {
            rebuildTrayMenu()
          }
        })
        .catch((error: unknown) => {
          const msg = error instanceof Error ? error.message : String(error)
          rememberLog(`[session] restore failed: ${msg}`)
          rebuildTrayMenu()
        })
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : String(error)
      rememberLog(`[session] restore failed: ${msg}`)
    }

    return bridgeDeps.backendSession
  },
  fetchJson,
  fileExists,
  getMainWindow: () => mainWindow,
  getSpriteWindow: () => mainWindow,
  getToolWindow: () => toolWindow,
  hideToolWindow,
  isQuitting: false,
  rebuildTrayMenu: () => rebuildTrayMenu(),
  rememberLog: (chunk: string) => rememberLog(chunk),
  resetBackendCache,
  resolveSpiritAgentVersion,
  rewireAuthToken: () => {
    getAuthToken = () => bridgeDeps.ensureBackendSession().getToken() ?? null
  },
  runnerBridge: null,
  safeStorage,
  showToolWindow,
  taggedLogger: (prefix: string) => (chunk: string) => rememberLog(`${prefix} ${chunk}`)
}

registerAuthIpc({ deps: bridgeDeps, ipcMain })
registerRunnerIpc({ deps: bridgeDeps, ipcMain })
registerRunnerConfigIpc({ ipcMain })
registerSkillsIpc({ spiritagentHome: SPIRITAGENT_HOME, getRunnerBridge: () => bridgeDeps.runnerBridge, ipcMain })
registerUpdateIpc({
  electron: { app },
  getMainWindow: () => mainWindow,
  ipcMain,
  sendToMain
})

registerSpriteIpc({
  deps: { getSpriteWindow: () => mainWindow, getUserDataDir: () => app.getPath('userData'), screen },
  ipcMain
})

ipcMain.handle(IPC.invoke.windowShowTool, async () => {
  showToolWindow()
})

ipcMain.handle(IPC.invoke.runnerGetTools, async () => {
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

setTimeout(() => {
  if (bridgeDeps.ensureBackendSession().getSession()?.hasToken) {
    autoStartBridge(bridgeDeps)
  }
}, 200).unref?.()

void app.whenReady().then(async () => {
  if (IS_MAC) {
    Menu.setApplicationMenu(buildApplicationMenu())
  } else {
    Menu.setApplicationMenu(null)
  }

  installMediaPermissions()
  installContentSecurityPolicy()
  registerMediaProtocol()
  configureSpellChecker()
  registerPowerResumeListeners()
  setupAutoUpdater()

  await getRunnerUpdater()
    .installPending()
    .catch(err => {
      log.warn('runner installPending failed:', err?.message || err)
    })
  createSpriteWindow()

  registerSingleInstanceForwarder({
    app,
    bridgeDeps,
    createWindow: createSpriteWindow,
    getAppIconPath,
    Menu,
    nativeImage,
    rememberLog,
    Tray
  })

  installTray({
    app,
    bridgeDeps,
    createWindow: createSpriteWindow,
    getAppIconPath,
    Menu,
    nativeImage,
    rememberLog,
    Tray
  })

  app.on('activate', () => {
    const win = bridgeDeps.getMainWindow()

    if (!win || win.isDestroyed()) {
      createSpriteWindow()
    } else {
      showMainWindow()
    }
  })
})

function configureSpellChecker(): void {
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
    const msg = error instanceof Error ? error.message : String(error)
    rememberLog(`Spellchecker setup failed: ${msg}`)
  }
}

app.on('before-quit', () => {
  bridgeDeps.isQuitting = true
  destroyTray()

  if (bridgeDeps.runnerBridge) {
    try {
      void bridgeDeps.runnerBridge.stop({ reason: 'app-quit' })
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error)
      rememberLog(`[runner-bridge] quit cleanup failed: ${msg}`)
    }
  }

  desktopLogger.flushSync()
})

app.on('window-all-closed', () => {
  if (bridgeDeps.isQuitting && !IS_MAC) {
    app.quit()
  }
})
