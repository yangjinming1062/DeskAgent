'use strict'

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
async function atomicWriteFile(targetPath, content) {
  await fs.promises.mkdir(path.dirname(targetPath), { recursive: true })
  const tmpPath = `${targetPath}.${process.pid}.${Date.now()}.tmp`

  try {
    await fs.promises.writeFile(tmpPath, content, 'utf8')
    await fs.promises.rename(tmpPath, targetPath)
  } catch (error) {
    await fs.promises.unlink(tmpPath).catch(() => {})
    throw error
  }
}

module.exports = { looksBinary, fileExists, directoryExists, sendToMain, atomicWriteFile }
