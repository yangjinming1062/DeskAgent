import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { registerOnboardingAudioIpc } from './onboarding-audio'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

function makeFakeIpc() {
  const handlers = new Map<string, (...args: any[]) => any>()

  return {
    handle: (channel: string, handler: (...args: any[]) => any) => handlers.set(channel, handler),
    invoke: (channel: string, ...args: any[]) => {
      const h = handlers.get(channel)

      if (!h) {
        throw new Error(`no handler for ${channel}`)
      }

      return h({}, ...args)
    }
  }
}

test('onboardingAudio:read resolves audio file from deskagentHome or dev fallback', async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'deskagent-audio-test-'))
  const ipcMain = makeFakeIpc()
  const resolveReadableFileForIpc = async (filePath: string) => ({ resolvedPath: path.resolve(filePath), stat: {} })
  const mimeTypeForPath = () => 'audio/mpeg'
  const devAudioRoot = path.resolve(__dirname, '../../../installer/payload/onboarding-audio/zh')

  registerOnboardingAudioIpc({
    deskagentHome: tmpDir,
    devAudioRoot,
    hardening: { resolveReadableFileForIpc },
    ipcMain: ipcMain as any,
    mimeTypeForPath
  })

  // 1. Tag matching repo payload falls back when tmpDir has no files
  const res = await ipcMain.invoke('deskagent:onboardingAudio:read', 'onboarding.q0')
  assert.equal(res.tag, 'onboarding.q0')
  assert.equal(res.mimeType, 'audio/mpeg')
  assert.ok(res.dataUrl.startsWith('data:audio/mpeg;base64,'))
  assert.ok(res.bytes > 0)

  // 2. Local deskagentHome file takes precedence over dev payload
  const localDir = path.join(tmpDir, 'audio', 'onboarding', 'zh')
  fs.mkdirSync(localDir, { recursive: true })
  fs.writeFileSync(path.join(localDir, 'onboarding.q0.mp3'), Buffer.from('fake-mp3-content'))

  const localRes = await ipcMain.invoke('deskagent:onboardingAudio:read', 'onboarding.q0')
  assert.equal(localRes.bytes, Buffer.from('fake-mp3-content').length)

  // 3. Invalid tags reject
  await assert.rejects(
    () => ipcMain.invoke('deskagent:onboardingAudio:read', '../invalid'),
    /invalid onboarding audio tag/
  )

  fs.rmSync(tmpDir, { force: true, recursive: true })
})
