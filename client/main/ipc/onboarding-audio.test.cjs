const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { registerOnboardingAudioIpc } = require('./onboarding-audio.cjs')

function makeFakeIpc() {
  const handlers = new Map()
  return {
    handle: (channel, handler) => handlers.set(channel, handler),
    invoke: (channel, ...args) => {
      const h = handlers.get(channel)
      if (!h) throw new Error(`no handler for ${channel}`)
      return h({}, ...args)
    }
  }
}

test('onboardingAudio:read resolves audio file from deskagentHome or dev fallback', async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'deskagent-audio-test-'))
  const ipcMain = makeFakeIpc()
  const resolveReadableFileForIpc = async filePath => ({ resolvedPath: path.resolve(filePath) })
  const mimeTypeForPath = () => 'audio/mpeg'

  registerOnboardingAudioIpc({
    ipcMain,
    deskagentHome: tmpDir,
    mimeTypeForPath,
    hardening: { resolveReadableFileForIpc }
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

  fs.rmSync(tmpDir, { recursive: true, force: true })
})
