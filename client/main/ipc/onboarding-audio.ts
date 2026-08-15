import fs from 'node:fs'
import path from 'node:path'

import type { App, IpcMain } from 'electron'

import { dataUrlFromBuffer } from '../shared/mime'

export const TAG_RE = /^onboarding\.[a-z0-9.]+$/
export const MAX_BYTES = 256 * 1024

export interface OnboardingAudioIpcDeps {
  app?: null | Partial<App>
  appRoot?: string
  deskagentHome?: null | string
  devAudioRoot?: string
  hardening: {
    resolveReadableFileForIpc: (
      filePath: string,
      options?: { maxBytes?: number; purpose?: string }
    ) => Promise<{ resolvedPath: string; stat: fs.Stats }>
  }
  ipcMain: IpcMain
  mimeTypeForPath: (filePath: string) => string
}

export function registerOnboardingAudioIpc({
  app,
  appRoot,
  deskagentHome,
  devAudioRoot: explicitDevAudioRoot,
  hardening,
  ipcMain,
  mimeTypeForPath
}: OnboardingAudioIpcDeps): void {
  if (!hardening) {
    throw new Error('registerOnboardingAudioIpc: hardening is required')
  }

  if (!deskagentHome) {
    throw new Error('registerOnboardingAudioIpc: deskagentHome is required')
  }

  if (typeof mimeTypeForPath !== 'function') {
    throw new Error('registerOnboardingAudioIpc: mimeTypeForPath is required')
  }

  const audioRoot = path.resolve(deskagentHome, 'audio', 'onboarding', 'zh')

  let devAudioRoot = explicitDevAudioRoot

  if (!devAudioRoot) {
    const baseAppPath = appRoot || (typeof app?.getAppPath === 'function' ? app.getAppPath() : process.cwd())
    // If baseAppPath is client/ or client/dist-electron, resolve to repo root
    let repoRoot = baseAppPath

    if (path.basename(repoRoot) === 'dist-electron') {
      repoRoot = path.dirname(repoRoot)
    }

    if (path.basename(repoRoot) === 'client') {
      repoRoot = path.dirname(repoRoot)
    }

    devAudioRoot = path.resolve(repoRoot, 'installer/payload/onboarding-audio/zh')
  }

  ipcMain.handle('deskagent:onboardingAudio:read', async (_event, tag: string) => {
    if (typeof tag !== 'string' || !TAG_RE.test(tag)) {
      throw new Error(`invalid onboarding audio tag: ${tag}`)
    }

    let targetPath = path.join(audioRoot, `${tag}.mp3`)

    if (!fs.existsSync(targetPath) && devAudioRoot) {
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

    return { bytes: data.length, dataUrl: dataUrlFromBuffer(data, mimeType), mimeType, tag }
  })
}
