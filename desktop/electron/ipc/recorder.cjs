const { BrowserWindow, net, screen } = require('electron')
const path = require('node:path')
const fs = require('node:fs')
const os = require('node:os')

const { sendToMain } = require('../utils.cjs')

const MAX_BUFFER_BYTES = 32 * 1024 * 1024 // 32 MB; spills to disk above this

// Throttle progress IPC events to ~10 Hz to avoid saturating the channel
// with hundreds of bytesUploaded deltas per second.
const PROGRESS_THROTTLE_MS = 100

// Toolbar window dimensions. Both the initial create and the per-move
// bounds-clamp must agree, or the window snaps to a different size on
// the next drag. The 720 width is sized to fit status dot + progress
// text + duration + mic + pause + stop buttons on a single line.
const TOOLBAR_WIDTH = 720
const TOOLBAR_HEIGHT = 60

function broadcast(channel, payload) {
  BrowserWindow.getAllWindows().forEach(win => {
    sendToMain(win, channel, payload)
  })
}

/**
 * POST a webm buffer to the Backend's `/api/media/recording/upload` endpoint.
 *
 * Uses ``electron.net.request`` because this handler runs in the **main
 * process**, where ``XMLHttpRequest`` is not a global (only the renderer
 * has it). ``net.request`` is Electron's main-process HTTP client and
 * exposes a ``'upload-progress'`` event with byte-level granularity, the
 * equivalent of XHR's ``xhr.upload.onprogress``.
 *
 * Fires ``zast:recorder:upload-started`` synchronously before the
 * request body is sent so the toolbar's renderer flips to the uploading
 * state on the next IPC tick — before the first progress event arrives —
 * and there's no flicker between ``onStopped`` and the first progress
 * update.
 *
 * Returns the gs:// URI parsed from the response body; rejects on any
 * non-2xx, network error, or abort.
 */
function uploadRecordingToBackend({ baseUrl, token, buffer }) {
  return new Promise((resolve, reject) => {
    const totalBytes = buffer.byteLength
    let lastEmittedAt = 0
    const startedAt = Date.now()

    function emitProgress(p) {
      const elapsedMs = p.atMs - startedAt
      const bytesPerSec = elapsedMs > 0 ? p.bytesSent / (elapsedMs / 1000) : 0
      const remaining = Math.max(0, totalBytes - p.bytesSent)
      const etaMs = bytesPerSec > 0 ? (remaining / bytesPerSec) * 1000 : null
      // Cap percent at 99 until ``recorder:finished`` fires — net.request
      // reports wire bytes (multipart envelope + trailing boundary)
      // which exceed the file size by a few hundred bytes near
      // completion, briefly showing 100% BEFORE the server has actually
      // received the last bytes. ``onFinished`` then snaps to 100%.
      const rawPercent = Math.floor((p.bytesSent / totalBytes) * 100)
      const payload = {
        bytesSent: p.bytesSent,
        totalBytes,
        percent: Math.min(99, rawPercent),
        bytesPerSec: Math.round(bytesPerSec),
        etaMs: etaMs != null ? Math.round(etaMs) : null
      }
      broadcast('zast:recorder:progress', payload)
    }

    // Manually build a multipart/form-data body — the main process has
    // no DOM ``FormData`` global. Concatenate into a single Buffer and
    // hand it to ``request.end(body)`` so the underlying stack can
    // compute Content-Length itself (manually setting Content-Length
    // via setHeader produces ``net::ERR_INVALID_ARGUMENT`` — those
    // headers are stack-managed and cannot be overridden).
    const boundary = `----zast-${Date.now()}-${Math.random().toString(36).slice(2)}`
    const CRLF = '\r\n'
    const headParts = [
      `--${boundary}${CRLF}`,
      `Content-Disposition: form-data; name="file"; filename="recording.webm"${CRLF}`,
      `Content-Type: video/webm${CRLF}${CRLF}`
    ]
    const fileHead = Buffer.from(headParts.join(''), 'utf-8')
    const middle = Buffer.from(
      `\r\n--${boundary}\r\nContent-Disposition: form-data; name="ext"\r\n\r\nwebm\r\n--${boundary}--\r\n`,
      'utf-8'
    )
    const body = Buffer.concat([fileHead, buffer, middle])

    const request = net.request({
      method: 'POST',
      url: `${baseUrl}/api/media/recording/upload`
    })
    request.setHeader('Authorization', `Bearer ${token}`)
    request.setHeader('Content-Type', `multipart/form-data; boundary=${boundary}`)

    let settled = false
    function settle(fn, arg) {
      if (settled) return
      settled = true
      fn(arg)
    }

    // Client-side hard timeout — 15 minutes matches the typical 30-min
    // recording worst case (115 MB @ 1 Mbps = 115 s, + slow network +
    // backend boto3 retries). Declared up front so event handlers below
    // can clearTimeout; ``const`` in a TDZ would crash at first use.
    const timeoutHandle = setTimeout(
      () => {
        try {
          request.abort()
        } catch {
          /* already settled */
        }
        settle(reject, new Error('upload timeout (15 min)'))
      },
      15 * 60 * 1000
    )

    // electron.net exposes no explicit timeout API; the underlying
    // socket follows the OS default (~minutes). The slowapi/per-user
    // rate limit + the toolbar's own clock-based UI act as the user-
    // visible signal; a hung socket will eventually error out via the
    // 'error' event below.
    request.on('upload-progress', (_event, bytesUploaded) => {
      const now = Date.now()
      if (now - lastEmittedAt < PROGRESS_THROTTLE_MS) return
      lastEmittedAt = now
      emitProgress({ bytesSent: bytesUploaded, totalBytes, atMs: now })
    })

    request.on('response', response => {
      const chunks = []
      response.on('data', chunk => chunks.push(chunk))
      response.on('end', () => {
        clearTimeout(timeoutHandle)
        const body = Buffer.concat(chunks).toString('utf-8')
        if (response.statusCode >= 200 && response.statusCode < 300) {
          let parsed
          try {
            parsed = JSON.parse(body)
          } catch (parseErr) {
            settle(reject, new Error(`upload succeeded but response was not JSON: ${parseErr.message}`))
            return
          }
          if (!parsed || typeof parsed.file_url !== 'string') {
            settle(reject, new Error(`upload response missing file_url: ${body.slice(0, 200)}`))
            return
          }
          settle(resolve, parsed.file_url)
        } else {
          // 502/503/504 from nginx/Cloudflare can carry multi-kilobyte
          // HTML error pages; try the structured `{error, reason}` envelope
          // first, fall back to a truncated raw text excerpt.
          let detail = ''
          try {
            const obj = JSON.parse(body)
            detail = obj.detail?.reason || obj.detail?.error || obj.reason || obj.error || ''
            if (typeof detail === 'object') detail = JSON.stringify(detail)
          } catch {
            detail = (body || '').slice(0, 200)
          }
          settle(reject, new Error(`upload failed: ${response.statusCode} ${detail}`))
        }
      })
    })

    request.on('error', err => {
      clearTimeout(timeoutHandle)
      settle(reject, new Error(`upload network error: ${err.message || err}`))
    })

    request.on('abort', () => {
      clearTimeout(timeoutHandle)
      settle(reject, new Error('upload aborted'))
    })

    // Fire upload-started synchronously before writing the body so the
    // toolbar's renderer flips to the uploading state on the next IPC tick.
    broadcast('zast:recorder:upload-started')

    try {
      request.end(body)
    } catch (syncErr) {
      // ``request.end`` can throw synchronously on malformed URLs or
      // destroyed sessions. Without this try/catch, the Promise executor
      // throws and the toolbar/composer stay stuck in the uploading
      // state.
      settle(reject, new Error(`upload write threw: ${syncErr.message}`))
    }
  })
}

let toolbarWindow = null
let recordingState = 'idle' // idle, recording, paused

// RecordingState wraps the in-memory chunk array + on-disk spill file so
// callers can't forget to keep two variables in sync (appendChunk previously
// had to maintain `recordingBytes` and the spill fd alongside the array).
const recording = {
  buffers: [],
  spillPath: null,
  spillFd: null,

  reset() {
    this.buffers = []
    if (this.spillFd !== null) {
      try {
        fs.closeSync(this.spillFd)
      } catch {
        // fd may already be closed (EBADF) on concurrent reset — safe to ignore.
      }
      this.spillFd = null
    }
    if (this.spillPath !== null) {
      try {
        fs.unlinkSync(this.spillPath)
      } catch {
        // file may already be gone (ENOENT) — spill was ephemeral, no recovery needed.
      }
      this.spillPath = null
    }
  },

  append(buffer) {
    this.buffers.push(buffer)
    if (this.byteLength() > MAX_BUFFER_BYTES) {
      this._ensureSpill()
      while (this.buffers.length > 1) {
        const chunk = this.buffers.shift()
        fs.writeSync(this.spillFd, chunk)
      }
    }
  },

  byteLength() {
    let n = 0
    for (const buf of this.buffers) n += buf.length
    if (this.spillFd !== null) {
      try {
        const stat = fs.fstatSync(this.spillFd)
        // Subtract the one in-memory chunk not yet flushed.
        n -= this.buffers[0]?.length ?? 0
        n += stat.size
      } catch {
        // stat may fail if fd was closed concurrently — byteLength is best-effort.
      }
    }
    return n
  },

  _ensureSpill() {
    if (this.spillFd !== null) return
    this.spillPath = path.join(os.tmpdir(), `zast-rec-${Date.now()}-${Math.random().toString(36).slice(2)}.webm`)
    this.spillFd = fs.openSync(this.spillPath, 'w')
  },

  snapshot() {
    return { buffers: this.buffers, spillPath: this.spillPath, spillFd: this.spillFd }
  },

  restore(snap) {
    this.buffers = snap.buffers
    this.spillPath = snap.spillPath
    this.spillFd = snap.spillFd
  },

  isEmpty() {
    // ``this.spillFd === null`` check is the live-state invariant — a
    // restored snap with a stale ``spillFd`` from before reset() would
    // pass the original check (spillPath !== null) and let a doomed
    // ``openSync(ENOENT)`` attempt through. The fd nullity tells us
    // whether the spill path is actually usable, not whether the path
    // string is non-empty.
    return this.buffers.length === 0 && this.spillFd === null
  },

  // Read the recorded bytes back into a single Buffer for upload. Accepts a
  // snapshot so callers operating on captured state don't have to mutate
  // the live object (and so the spill file can be read BEFORE reset()
  // unlinks it — see finishUpload). The caller decides whether to unlink
  // the spill on success or restore on failure.
  //
  // When a spill exists, ``append()`` always leaves exactly ONE in-memory
  // chunk (the tail) — older chunks are flushed to the spill file. We must
  // concatenate the spill bytes with that tail, otherwise the uploaded
  // webm is missing its final ~MediaRecorder-timeslice of bytes.
  assemble(snap) {
    const spillPath = snap.spillPath
    const tailBuffers = snap.buffers
    const tail = Buffer.concat(tailBuffers)
    if (spillPath === null) {
      return tail
    }
    const fd = fs.openSync(spillPath, 'r')
    try {
      const stat = fs.fstatSync(fd)
      // Single allocation + one tail copy — avoids the second buffer
      // allocation that ``Buffer.concat([head, tail])`` would create.
      const buf = Buffer.allocUnsafe(stat.size + tail.length)
      fs.readSync(fd, buf, 0, stat.size, 0)
      tail.copy(buf, stat.size)
      return buf
    } finally {
      fs.closeSync(fd)
    }
  }
}

function isToolbarSender(event) {
  return toolbarWindow !== null && !toolbarWindow.isDestroyed() && event.sender === toolbarWindow.webContents
}

function clampBoundsToWorkArea(bounds) {
  const display = screen.getDisplayMatching(bounds) || screen.getPrimaryDisplay()
  const wa = display.workArea
  const maxX = wa.x + wa.width - bounds.width
  const maxY = wa.y + wa.height - bounds.height
  return {
    x: Math.max(wa.x, Math.min(bounds.x, maxX)),
    y: Math.max(wa.y, Math.min(bounds.y, maxY)),
    width: bounds.width,
    height: bounds.height
  }
}

function createToolbarWindow(senderUrl) {
  const primaryDisplay = screen.getPrimaryDisplay()
  const { width, height } = primaryDisplay.workAreaSize

  const win = new BrowserWindow({
    width: TOOLBAR_WIDTH,
    height: TOOLBAR_HEIGHT,
    x: width / 2 - TOOLBAR_WIDTH / 2,
    y: height - 100,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    resizable: false,
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false
    }
  })

  if (senderUrl) {
    const target = new URL(senderUrl)
    target.hash = '#/toolbar'
    win.loadURL(target.toString())
  }
  return win
}

function registerRecorderIpc({ ipcMain, deps }) {
  ipcMain.handle('zast:recorder:startWithToolbar', async event => {
    if (!toolbarWindow || toolbarWindow.isDestroyed()) {
      toolbarWindow = createToolbarWindow(event.sender.getURL())
    } else if (toolbarWindow.isMinimized()) {
      toolbarWindow.restore()
    }
    toolbarWindow.show()
    recordingState = 'recording'
    recording.reset()
    return true
  })

  ipcMain.handle('zast:recorder:pause', async event => {
    if (!isToolbarSender(event)) throw new Error('unauthorized')
    recordingState = 'paused'
    return true
  })

  ipcMain.handle('zast:recorder:resume', async event => {
    if (!isToolbarSender(event)) throw new Error('unauthorized')
    recordingState = 'recording'
    return true
  })

  ipcMain.handle('zast:recorder:stop', async event => {
    if (!isToolbarSender(event)) throw new Error('unauthorized')
    recordingState = 'idle'
    if (toolbarWindow && !toolbarWindow.isDestroyed()) {
      toolbarWindow.close()
    }
    toolbarWindow = null
    return true
  })

  ipcMain.handle('zast:recorder:setData', async (event, bufferOrBlob) => {
    if (!isToolbarSender(event)) throw new Error('unauthorized')
    if (recordingState !== 'recording') return true // drop chunks after stop
    let buffer
    if (bufferOrBlob instanceof ArrayBuffer) {
      buffer = Buffer.from(bufferOrBlob)
    } else if (ArrayBuffer.isView(bufferOrBlob)) {
      buffer = Buffer.from(bufferOrBlob.buffer, bufferOrBlob.byteOffset, bufferOrBlob.byteLength)
    } else if (typeof Blob !== 'undefined' && bufferOrBlob instanceof Blob) {
      buffer = Buffer.from(await bufferOrBlob.arrayBuffer())
    } else {
      throw new Error('setData expects ArrayBuffer / TypedArray / Blob')
    }
    recording.append(buffer)
    return true
  })

  ipcMain.handle('zast:recorder:finishUpload', async event => {
    if (!isToolbarSender(event)) throw new Error('unauthorized')
    if (recording.isEmpty()) throw new Error('No recording data found')

    // Notify non-toolbar windows that recording has stopped so the
    // composer's ScreenRecordButton flips out of its "in-progress" highlight.
    // The toolbar self-closes via api.stop() so it doesn't need this.
    BrowserWindow.getAllWindows().forEach(win => {
      if (win === toolbarWindow) return
      sendToMain(win, 'zast:recorder:stopped')
    })

    // Snapshot first — DO NOT reset yet. ``recording.reset()`` closes the
    // spill fd AND unlinks the spill file, so we must read the spill into
    // the upload buffer BEFORE clearing state. Reset only fires inside
    // the try block (after upload succeeds); a failure before then leaves
    // the recording state intact via the catch's ``recording.restore(snap)``.
    const snap = recording.snapshot()
    const finalBuffer = recording.assemble(snap)

    try {
      const connection = await deps.ensureBackend()
      const fileUrl = await uploadRecordingToBackend({
        baseUrl: connection.baseUrl,
        token: connection.token,
        buffer: finalBuffer
      })

      // SUCCESS: tear down recording state. ``reset()`` closes the spill
      // fd and unlinks the spill file as part of clearing state — no
      // separate cleanup pass needed.
      recording.reset()

      // Toolbar needs the finished event to snap its progress to 100% and close.
      broadcast('zast:recorder:finished', fileUrl)

      return fileUrl
    } catch (err) {
      // FAILURE: restore the in-memory + spill state so a retry can reuse
      // it. The toolbar's handleStop catch MUST NOT show its own toast —
      // only `onFailed` is allowed to surface the error message.
      recording.restore(snap)
      const message = err && err.message ? err.message : String(err)
      broadcast('zast:recorder:failed', { message })
      throw err
    }
  })

  ipcMain.handle('zast:recorder:hideToolbar', async event => {
    if (!isToolbarSender(event)) throw new Error('unauthorized')
    if (toolbarWindow && !toolbarWindow.isDestroyed()) toolbarWindow.hide()
    return true
  })

  ipcMain.handle('zast:recorder:moveToolbar', async (event, { x, y }) => {
    if (!isToolbarSender(event)) throw new Error('unauthorized')
    if (!toolbarWindow || toolbarWindow.isDestroyed()) return true
    if (typeof x !== 'number' || typeof y !== 'number') {
      throw new Error('moveToolbar expects numeric x, y')
    }
    const clamped = clampBoundsToWorkArea({ x, y, width: TOOLBAR_WIDTH, height: TOOLBAR_HEIGHT })

    toolbarWindow.setBounds({
      x: Math.round(clamped.x),
      y: Math.round(clamped.y),
      width: TOOLBAR_WIDTH,
      height: TOOLBAR_HEIGHT
    })
    return true
  })
}

module.exports = { registerRecorderIpc }
