import assert from 'node:assert'
import fs from 'node:fs'
import path from 'node:path'
import { test } from 'node:test'

const ROOT = path.resolve(import.meta.dirname, '..', '..')

function read(rel: string): string {
  return fs.readFileSync(path.join(ROOT, rel), 'utf8')
}

const SOURCES = {
  'constants.py': read('runner/utils/constants.py'),
  'install.ps1': read('installer/install.ps1'),
  'install.sh': read('installer/install.sh'),
  'paths.rs': read('installer/src-tauri/src/paths.rs'),
  'paths.ts': read('client/main/security/paths.ts')
}

function extractMatch(src: string, pattern: RegExp, label: string): string {
  const m = src.match(pattern)
  assert(m, `${label}: pattern did not match`)

  return m[1]
}

test('SPIRITAGENT_HOME: all resolvers use the same Windows directory name', () => {
  const dirs = new Set<string>()
  dirs.add(extractMatch(SOURCES['constants.py'], /Path\(local_appdata\)\s*\/\s*"([^"]+)"/, 'constants.py'))
  dirs.add(extractMatch(SOURCES['paths.ts'], /path\.join\(local,\s*'([^']+)'\)/, 'paths.ts'))
  dirs.add(extractMatch(SOURCES['paths.rs'], /data_local_dir\(\)[\s\S]*?\.join\("([^"]+)"\)/, 'paths.rs'))
  dirs.add(extractMatch(SOURCES['install.ps1'], /Join-Path\s+\$env:LOCALAPPDATA\s+"([^"]+)"/, 'install.ps1'))
  assert.strictEqual(dirs.size, 1, `Windows dir name drift: ${[...dirs].join(' vs ')}`)
})

test('SPIRITAGENT_HOME: all resolvers use the same macOS directory name', () => {
  const dirs = new Set<string>()
  dirs.add(extractMatch(SOURCES['constants.py'], /"Application Support"\s*\/\s*"([^"]+)"/, 'constants.py'))
  dirs.add(extractMatch(SOURCES['paths.ts'], /'Application Support',\s*'([^']+)'/, 'paths.ts'))
  dirs.add(extractMatch(SOURCES['paths.rs'], /"Library\/Application Support\/([^"]+)"/, 'paths.rs'))
  dirs.add(extractMatch(SOURCES['install.sh'], /Library\/Application Support\/([^"]+)"/, 'install.sh'))
  assert.strictEqual(dirs.size, 1, `macOS dir name drift: ${[...dirs].join(' vs ')}`)
})
