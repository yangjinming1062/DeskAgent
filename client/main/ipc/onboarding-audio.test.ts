import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import type { IpcMain, IpcMainInvokeEvent } from 'electron'

import { registerOnboardingAudioIpc } from './onboarding-audio'

type Handler = (event: IpcMainInvokeEvent, tag: string) => Promise<unknown> | unknown

function makeFakeIpc(): {
  handle: (channel: string, handler: Handler) => void
  invoke: (channel: string, ...args: unknown[]) => Promise<unknown>
} {
  const handlers = new Map<string, Handler>()

  return {
    handle: (channel, handler) => {
      handlers.set(channel, handler)
    },
    invoke: async (channel, ...args) => {
      const h = handlers.get(channel)

      if (!h) {
        throw new Error(`no handler for ${channel}`)
      }

      return h({} as IpcMainInvokeEvent, args[0] as string)
    }
  }
}

test('onboardingAudio:read resolves audio file from spiritagentHome or dev fallback', async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'spiritagent-audio-test-'))
  const ipcMain = makeFakeIpc()

  const resolveReadableFileForIpc = async (filePath: string) => ({
    resolvedPath: path.resolve(filePath),
    stat: {} as fs.Stats
  })

  const mimeTypeForPath = () => 'audio/mpeg'
  const devAudioRoot = path.resolve(import.meta.dirname, '../../../installer/payload/onboarding-audio/zh')

  registerOnboardingAudioIpc({
    spiritagentHome: tmpDir,
    devAudioRoot,
    hardening: { resolveReadableFileForIpc },
    ipcMain: ipcMain as unknown as IpcMain,
    mimeTypeForPath
  })

  // 1. Tag matching repo payload falls back when tmpDir has no files
  const res = await ipcMain.invoke('spiritagent:onboardingAudio:read', 'onboarding.q0')
  assert.equal((res as { tag: string }).tag, 'onboarding.q0')
  assert.equal((res as { mimeType: string }).mimeType, 'audio/mpeg')
  assert.ok((res as { dataUrl: string }).dataUrl.startsWith('data:audio/mpeg;base64,'))
  assert.ok((res as { bytes: number }).bytes > 0)

  // 2. Local spiritagentHome file takes precedence over dev payload
  const localDir = path.join(tmpDir, 'audio', 'onboarding', 'zh')
  fs.mkdirSync(localDir, { recursive: true })
  fs.writeFileSync(path.join(localDir, 'onboarding.q0.mp3'), Buffer.from('fake-mp3-content'))

  const localRes = await ipcMain.invoke('spiritagent:onboardingAudio:read', 'onboarding.q0')
  assert.equal((localRes as { bytes: number }).bytes, Buffer.from('fake-mp3-content').length)

  // 3. Invalid tags reject
  await assert.rejects(
    () => ipcMain.invoke('spiritagent:onboardingAudio:read', '../invalid'),
    /invalid onboarding audio tag/
  )

  fs.rmSync(tmpDir, { force: true, recursive: true })
})
