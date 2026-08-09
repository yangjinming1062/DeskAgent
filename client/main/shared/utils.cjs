'use strict'

const crypto = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')

function looksBinary(buffer) {
  if (!buffer.length) return false

  let suspicious = 0

  for (const byte of buffer) {
    if (byte === 0) return true
    // Allow common whitespace controls: tab, LF, CR.
    if (byte < 32 && byte !== 9 && byte !== 10 && byte !== 13) suspicious += 1
  }

  return suspicious / buffer.length > 0.12
}

function fileExists(filePath) {
  try {
    return fs.statSync(filePath).isFile()
  } catch {
    return false
  }
}

function directoryExists(filePath) {
  try {
    return fs.statSync(filePath).isDirectory()
  } catch {
    return false
  }
}

// Guard for mainWindow.webContents.send(...): skip if destroyed (race during shutdown/reload).
function sendToMain(mainWindow, channel, payload) {
  if (!mainWindow || mainWindow.isDestroyed()) return
  const { webContents } = mainWindow
  if (!webContents || webContents.isDestroyed()) return
  webContents.send(channel, payload)
}

// Write-then-rename so a crash mid-write leaves the previous file intact.
// Unlinks the .tmp on failure to avoid accumulating orphans across crashed saves.
// Concurrent bake writers against the same target (e.g. reaction audio IPC
// with 10-way fan-out) need a tmp name that doesn't collide on millisecond
// boundaries; the UUID segment keeps each writer's tmp path unique.
// `writeFile` omits the encoding arg so string callers get utf8 (Node default)
// and Buffer callers get binary without per-call branching.
async function atomicWriteFile(targetPath, content) {
  await fs.promises.mkdir(path.dirname(targetPath), { recursive: true })
  const tmpPath = `${targetPath}.${process.pid}.${crypto.randomUUID()}.tmp`

  try {
    await fs.promises.writeFile(tmpPath, content)
    await fs.promises.rename(tmpPath, targetPath)
  } catch (error) {
    await fs.promises.unlink(tmpPath).catch(() => {})
    throw error
  }
}

// Resolves after `ms` milliseconds. Single shared implementation of the
// `await new Promise(r => setTimeout(r, ms))` idiom that's been copied into
// 11+ files — used for IPC retry backoff, TTS inter-call throttling, and
// test fixture pacing.
function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

module.exports = { looksBinary, fileExists, directoryExists, sendToMain, atomicWriteFile, sleep }
