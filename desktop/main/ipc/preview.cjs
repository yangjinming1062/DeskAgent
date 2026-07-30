'use strict'

const path = require('node:path')
const fs = require('node:fs')
const crypto = require('node:crypto')
const { fileURLToPath, pathToFileURL } = require('node:url')
const { app } = require('electron')
const { fileExists, directoryExists, sendToMain } = require('../shared/utils.cjs')

const PREVIEW_HTML_EXTENSIONS = new Set(['.html', '.htm'])
const PREVIEW_WATCH_DEBOUNCE_MS = 120
const LOCAL_PREVIEW_HOSTS = new Set(['0.0.0.0', '127.0.0.1', '::1', '[::1]', 'localhost'])

function previewLabelForUrl(url) {
  return `${url.host}${url.pathname === '/' ? '' : url.pathname}`
}

function expandUserPath(filePath) {
  const value = String(filePath || '').trim()

  if (value === '~') {
    return app.getPath('home')
  }

  if (value.startsWith(`~${path.sep}`) || value.startsWith('~/')) {
    return path.join(app.getPath('home'), value.slice(2))
  }

  return value
}

async function previewFileTarget(rawTarget, baseDir, deps) {
  const { resolveDeskAgentCwd, previewFileMetadata, mimeTypeForPath, previewLanguageByExt } = deps
  const raw = String(rawTarget || '').trim()
  const base = baseDir ? path.resolve(expandUserPath(baseDir)) : resolveDeskAgentCwd()
  const filePath = raw.startsWith('file:') ? fileURLToPath(raw) : path.resolve(base, expandUserPath(raw))
  let resolved = filePath

  if (directoryExists(resolved)) {
    resolved = path.join(resolved, 'index.html')
  }

  const ext = path.extname(resolved).toLowerCase()
  if (!fileExists(resolved)) {
    return null
  }

  const mimeType = mimeTypeForPath(resolved)
  const metadata = await previewFileMetadata(resolved, mimeType)
  const isHtml = PREVIEW_HTML_EXTENSIONS.has(ext)
  const isImage = mimeType.startsWith('image/')
  const previewKind = isHtml ? 'html' : isImage ? 'image' : metadata.binary ? 'binary' : 'text'

  return {
    binary: metadata.binary,
    byteSize: metadata.byteSize,
    kind: 'file',
    large: metadata.large,
    label: path.basename(resolved),
    language: previewLanguageByExt[ext] || 'text',
    mimeType,
    path: resolved,
    previewKind,
    source: raw,
    url: pathToFileURL(resolved).toString()
  }
}

function previewUrlTarget(rawTarget) {
  const raw = String(rawTarget || '').trim()
  const url = new URL(raw)

  if (!['http:', 'https:'].includes(url.protocol)) {
    return null
  }

  if (!LOCAL_PREVIEW_HOSTS.has(url.hostname.toLowerCase())) {
    return null
  }

  if (url.hostname === '0.0.0.0') {
    url.hostname = '127.0.0.1'
  }

  return {
    kind: 'url',
    label: previewLabelForUrl(url),
    source: raw,
    url: url.toString()
  }
}

async function normalizePreviewTarget(rawTarget, baseDir, deps) {
  const raw = String(rawTarget || '').trim()

  if (!raw) {
    return null
  }

  try {
    if (/^https?:\/\//i.test(raw)) {
      return previewUrlTarget(raw)
    }

    return await previewFileTarget(raw, baseDir, deps)
  } catch {
    return null
  }
}

function filePathFromPreviewUrl(rawUrl) {
  const filePath = fileURLToPath(String(rawUrl || ''))

  if (!fileExists(filePath)) {
    throw new Error('Preview file is not readable')
  }

  return filePath
}

function sendPreviewFileChanged(getMainWindow, payload) {
  sendToMain(getMainWindow(), 'deskagent:preview-file-changed', payload)
}

function watchPreviewFile(rawUrl, deps) {
  const { previewWatchers, getMainWindow } = deps
  const filePath = filePathFromPreviewUrl(rawUrl)
  const watchDir = path.dirname(filePath)
  const targetName = path.basename(filePath)
  const id = crypto.randomBytes(12).toString('base64url')
  let timer = null
  const watcher = fs.watch(watchDir, (_eventType, filename) => {
    const changedName = filename ? path.basename(String(filename)) : ''

    if (changedName && changedName !== targetName) {
      return
    }

    if (timer) clearTimeout(timer)
    timer = setTimeout(() => {
      timer = null
      if (!fileExists(filePath)) return
      sendPreviewFileChanged(getMainWindow, {
        id,
        path: filePath,
        url: pathToFileURL(filePath).toString()
      })
    }, PREVIEW_WATCH_DEBOUNCE_MS)
  })

  previewWatchers.set(id, {
    close: () => {
      if (timer) clearTimeout(timer)
      watcher.close()
    }
  })

  return { id, path: filePath }
}

function stopPreviewFileWatch(id, deps) {
  const watcher = deps.previewWatchers.get(id)

  if (!watcher) {
    return false
  }

  watcher.close()
  deps.previewWatchers.delete(id)

  return true
}

function closePreviewWatchers(deps) {
  for (const id of [...deps.previewWatchers.keys()]) {
    stopPreviewFileWatch(id, deps)
  }
}

function registerPreviewIpc({ ipcMain, deps }) {
  ipcMain.handle('deskagent:normalizePreviewTarget', async (_event, target, baseDir) =>
    normalizePreviewTarget(String(target || ''), baseDir ? String(baseDir) : '', deps)
  )

  ipcMain.handle('deskagent:watchPreviewFile', (_event, url) => watchPreviewFile(String(url || ''), deps))

  ipcMain.handle('deskagent:stopPreviewFileWatch', (_event, id) => stopPreviewFileWatch(String(id || ''), deps))
}

module.exports = { registerPreviewIpc, closePreviewWatchers }
