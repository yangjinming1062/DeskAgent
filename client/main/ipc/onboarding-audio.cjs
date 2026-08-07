'use strict'

const fs = require('node:fs')
const path = require('node:path')

const { dataUrlFromBuffer } = require('../shared/mime.cjs')

const TAG_RE = /^onboarding\.[a-z0-9.]+$/
// ~50KB expected per clip; 256KB cap absorbs quiet/wide-form variations without
// letting a misplaced large file blow up the IPC payload.
const MAX_BYTES = 256 * 1024

function registerOnboardingAudioIpc({ ipcMain, deskagentHome, mimeTypeForPath, hardening }) {
  if (!hardening) throw new Error('registerOnboardingAudioIpc: hardening is required')
  if (!deskagentHome) throw new Error('registerOnboardingAudioIpc: deskagentHome is required')
  if (typeof mimeTypeForPath !== 'function') throw new Error('registerOnboardingAudioIpc: mimeTypeForPath is required')

  const audioRoot = path.resolve(deskagentHome, 'audio', 'onboarding', 'zh')
  const devAudioRoot = path.resolve(__dirname, '../../../installer/payload/onboarding-audio/zh')

  ipcMain.handle('deskagent:onboardingAudio:read', async (_event, tag) => {
    if (typeof tag !== 'string' || !TAG_RE.test(tag)) {
      throw new Error(`invalid onboarding audio tag: ${tag}`)
    }

    let targetPath = path.join(audioRoot, `${tag}.mp3`)
    if (!fs.existsSync(targetPath)) {
      const devPath = path.join(devAudioRoot, `${tag}.mp3`)
      if (fs.existsSync(devPath)) {
        targetPath = devPath
      }
    }

    const { resolvedPath } = await hardening.resolveReadableFileForIpc(targetPath, {
      maxBytes: MAX_BYTES,
      purpose: 'Onboarding audio'
    })
    const data = await fs.promises.readFile(resolvedPath)
    const mimeType = mimeTypeForPath(resolvedPath)
    return { dataUrl: dataUrlFromBuffer(data, mimeType), mimeType, tag, bytes: data.length }
  })
}

module.exports = { registerOnboardingAudioIpc }
