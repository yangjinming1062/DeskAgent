'use strict'

const { spawn } = require('node:child_process')
const { app, BrowserWindow, session } = require('electron')

// Two-tier link title fetcher with bounded concurrency.
//   Tier 1: spawn `curl` to grab the raw HTML and extract <title>.
//   Tier 2: hidden BrowserWindow (deskagent:link-titles partition) for JS-heavy
//           pages curl can't render.
// All work funnels through a single in-flight map + LRU cache; multiple
// concurrent `deskagent:fetchLinkTitle` IPCs for the same URL share one promise.

const TITLE_CACHE_LIMIT = 500
const TITLE_BYTE_BUDGET = 96 * 1024
const TITLE_TIMEOUT_MS = 5000
const TITLE_MAX_REDIRECTS = 3

const TITLE_USER_AGENT = 'Mozilla/5.0 (compatible; DeskAgentTitleBot/1.0)'

const TITLE_ERROR_RE = /(?:404|forbidden|access\s*denied|not\s*found|error)/i

const HTML_ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', '#39': "'" }

const RENDER_TITLE_MAX_CONCURRENT = 2
const RENDER_TITLE_TIMEOUT_MS = 8000
const RENDER_TITLE_GRACE_MS = 700

const RENDER_TITLE_BLOCKED_RESOURCES = new Set(['image', 'media', 'font', 'stylesheet'])

const titleCache = new Map()
const titleInflight = new Map()

let linkTitleSession = null
let renderTitleInFlight = 0
const renderTitleQueue = []

function canonicalTitleCacheKey(rawUrl) {
  const value = String(rawUrl || '').trim()
  if (!value) return ''

  try {
    const url = new URL(value)
    const host = url.hostname.replace(/^www\./i, '').toLowerCase()
    const pathname = url.pathname === '/' ? '/' : url.pathname.replace(/\/+$/, '') || '/'

    return `${host}${pathname}${url.search || ''}`
  } catch {
    return value
  }
}

function cacheTitle(key, title) {
  if (titleCache.size >= TITLE_CACHE_LIMIT) titleCache.delete(titleCache.keys().next().value)
  titleCache.set(key, title)
}

function decodeHtmlEntities(value) {
  return value
    .replace(/&(amp|lt|gt|quot|apos|nbsp|#39);/gi, (_, k) => HTML_ENTITIES[k.toLowerCase()] ?? '')
    .replace(/&#x([0-9a-f]+);/gi, (_, hex) => String.fromCodePoint(parseInt(hex, 16) || 32))
    .replace(/&#(\d+);/g, (_, dec) => String.fromCodePoint(parseInt(dec, 10) || 32))
}

function parseHtmlTitle(html) {
  const raw = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1]
  return raw ? decodeHtmlEntities(raw).replace(/\s+/g, ' ').trim() : ''
}

function fetchHtmlTitleWithCurl(rawUrl) {
  return new Promise(resolve => {
    const url = String(rawUrl || '').trim()
    if (!url) return resolve('')

    const args = [
      '--silent',
      '--show-error',
      '--location',
      '--max-redirs',
      String(TITLE_MAX_REDIRECTS),
      '--max-time',
      String(Math.max(2, Math.ceil(TITLE_TIMEOUT_MS / 1000))),
      '--connect-timeout',
      '4',
      '--user-agent',
      TITLE_USER_AGENT,
      '--header',
      'Accept: text/html,application/xhtml+xml;q=0.9,*/*;q=0.5',
      '--header',
      'Accept-Language: en-US,en;q=0.7',
      '--header',
      'Accept-Encoding: identity',
      '--raw',
      url
    ]
    const child = spawn('curl', args, { stdio: ['ignore', 'pipe', 'ignore'] })
    const chunks = []
    let bytes = 0

    child.stdout.on('data', chunk => {
      if (bytes >= TITLE_BYTE_BUDGET) return
      const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)
      const remaining = TITLE_BYTE_BUDGET - bytes
      const next = buffer.length > remaining ? buffer.subarray(0, remaining) : buffer
      chunks.push(next)
      bytes += next.length
    })

    child.on('error', error => {
      console.warn(`[link-title] curl spawn failed: ${url} ${error?.message || error}`)
      resolve('')
    })
    child.on('close', () => {
      if (!chunks.length) return resolve('')
      resolve(parseHtmlTitle(Buffer.concat(chunks).toString('utf8')))
    })
  })
}

function getLinkTitleSession() {
  if (linkTitleSession || !app.isReady()) return linkTitleSession
  linkTitleSession = session.fromPartition('deskagent:link-titles', { cache: false })
  linkTitleSession.webRequest.onBeforeRequest((details, callback) => {
    callback({ cancel: RENDER_TITLE_BLOCKED_RESOURCES.has(details.resourceType) })
  })
  return linkTitleSession
}

function dequeueRenderTitle() {
  while (renderTitleInFlight < RENDER_TITLE_MAX_CONCURRENT && renderTitleQueue.length) {
    const item = renderTitleQueue.shift()
    renderTitleInFlight += 1
    runRenderTitleJob(item.url).then(title => {
      renderTitleInFlight -= 1
      item.resolve(title)
      dequeueRenderTitle()
    })
  }
}

function runRenderTitleJob(rawUrl) {
  return new Promise(resolve => {
    if (!app.isReady()) return resolve('')

    const partitionSession = getLinkTitleSession()
    if (!partitionSession) return resolve('')

    let settled = false
    let window = null
    let hardTimer = null
    let graceTimer = null

    const finish = title => {
      if (settled) return
      settled = true
      if (hardTimer) clearTimeout(hardTimer)
      if (graceTimer) clearTimeout(graceTimer)
      const value = (title || '').replace(/\s+/g, ' ').trim()
      try {
        if (window && !window.isDestroyed()) window.destroy()
      } catch {
        // BrowserWindow may already be torn down; ignore.
      }
      resolve(value)
    }

    try {
      window = new BrowserWindow({
        show: false,
        width: 1280,
        height: 800,
        webPreferences: {
          backgroundThrottling: false,
          contextIsolation: true,
          javascript: true,
          nodeIntegration: false,
          sandbox: true,
          session: partitionSession,
          webSecurity: true
        }
      })
    } catch {
      return finish('')
    }

    const readTitle = () => window?.webContents?.getTitle?.() || ''
    const scheduleGrace = () => {
      if (graceTimer) clearTimeout(graceTimer)
      graceTimer = setTimeout(() => finish(readTitle()), RENDER_TITLE_GRACE_MS)
    }

    hardTimer = setTimeout(() => finish(readTitle()), RENDER_TITLE_TIMEOUT_MS)

    window.webContents.setUserAgent(TITLE_USER_AGENT)
    window.webContents.on('page-title-updated', scheduleGrace)
    window.webContents.on('did-finish-load', scheduleGrace)
    window.webContents.on('did-fail-load', (_event, _code, _desc, _validatedURL, isMainFrame) => {
      if (isMainFrame) finish('')
    })

    window
      .loadURL(rawUrl, {
        httpReferrer: 'https://www.google.com/',
        userAgent: TITLE_USER_AGENT
      })
      .catch(error => {
        console.warn(`[link-title] loadURL failed: ${rawUrl} ${error?.message || error}`)
        finish('')
      })
  })
}

function fetchHtmlTitleWithRenderer(rawUrl) {
  return new Promise(resolve => {
    renderTitleQueue.push({ resolve, url: rawUrl })
    dequeueRenderTitle()
  })
}

const usableTitle = value => (value && !TITLE_ERROR_RE.test(value) ? value : '')

function fetchLinkTitle(rawUrl) {
  const url = String(rawUrl || '').trim()
  const key = canonicalTitleCacheKey(url)
  if (!key) return Promise.resolve('')
  if (titleCache.has(key)) return Promise.resolve(titleCache.get(key))
  if (titleInflight.has(key)) return titleInflight.get(key)

  const pending = fetchHtmlTitleWithCurl(url)
    .catch(error => {
      console.warn(`[link-title] curl chain failed: ${url} ${error?.message || error}`)
      return ''
    })
    .then(value => usableTitle((value || '').slice(0, 240)))
    .then(
      async value =>
        value ||
        usableTitle(
          (
            (await fetchHtmlTitleWithRenderer(url).catch(error => {
              console.warn(`[link-title] renderer failed: ${url} ${error?.message || error}`)
              return ''
            })) || ''
          ).slice(0, 240)
        )
    )
    .then(clean => {
      cacheTitle(key, clean)
      titleInflight.delete(key)
      return clean
    })

  titleInflight.set(key, pending)
  return pending
}

function registerLinkTitleIpc({ ipcMain }) {
  ipcMain.handle('deskagent:fetchLinkTitle', (_event, url) => fetchLinkTitle(url))
}

module.exports = { registerLinkTitleIpc }
