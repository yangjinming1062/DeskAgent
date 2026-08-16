import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import { createDesktopLogger, DESKTOP_LOG_DISCARD_BYTES, DESKTOP_LOG_MAX_BYTES } from './desktop-log'

test('createDesktopLogger remembers logs and flushes to disk', async () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'spiritagent-log-test-'))
  const logger = createDesktopLogger({ spiritagentHome: tmpDir, isPackaged: true })

  logger.rememberLog('first line')
  logger.rememberLog('second line')

  assert.equal(logger.getLogs().length, 2)
  assert.equal(logger.getLogs()[0], '[spiritagent] first line')

  await logger.flushAsync()

  const content = fs.readFileSync(logger.logPath, 'utf8')
  assert.ok(content.includes('[spiritagent] first line'))
  assert.ok(content.includes('[spiritagent] second line'))
})

test('createDesktopLogger plans rotation correctly', () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'spiritagent-log-rot-'))
  const logger = createDesktopLogger({ spiritagentHome: tmpDir, isPackaged: true })

  assert.deepEqual(logger.planRotation(100), [])

  const rotateOps = logger.planRotation(DESKTOP_LOG_MAX_BYTES + 1)
  assert.ok(rotateOps.length > 0)
  assert.equal(rotateOps[0][0], 'rm')

  const discardOps = logger.planRotation(DESKTOP_LOG_DISCARD_BYTES + 1)
  assert.ok(discardOps.length > 0)
  assert.ok(discardOps.every(op => op[0] === 'rm'))
})
