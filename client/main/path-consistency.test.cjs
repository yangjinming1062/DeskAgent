const fs = require('node:fs')
const path = require('node:path')
const { test } = require('node:test')
const assert = require('node:assert')

const ROOT = path.resolve(__dirname, '..', '..')

function read(rel) {
  return fs.readFileSync(path.join(ROOT, rel), 'utf8')
}

const SOURCES = {
  'constants.py': read('runner/utils/constants.py'),
  'paths.cjs': read('client/main/security/paths.cjs'),
  'paths.rs': read('installer/src-tauri/src/paths.rs'),
  'install.ps1': read('installer/install.ps1'),
  'install.sh': read('installer/install.sh')
}

function extractMatch(src, pattern, label) {
  const m = src.match(pattern)
  assert(m, `${label}: pattern did not match`)
  return m[1]
}

test('DESKAGENT_HOME: all resolvers use the same Windows directory name', () => {
  const dirs = new Set()
  dirs.add(extractMatch(SOURCES['constants.py'], /Path\(local_appdata\)\s*\/\s*"([^"]+)"/, 'constants.py'))
  dirs.add(extractMatch(SOURCES['paths.cjs'], /path\.join\(local,\s*'([^']+)'\)/, 'paths.cjs'))
  dirs.add(extractMatch(SOURCES['paths.rs'], /data_local_dir\(\)[\s\S]*?\.join\("([^"]+)"\)/, 'paths.rs'))
  dirs.add(extractMatch(SOURCES['install.ps1'], /Join-Path\s+\$env:LOCALAPPDATA\s+"([^"]+)"/, 'install.ps1'))
  assert.strictEqual(dirs.size, 1, `Windows dir name drift: ${[...dirs].join(' vs ')}`)
})

test('DESKAGENT_HOME: all resolvers use the same macOS directory name', () => {
  const dirs = new Set()
  dirs.add(extractMatch(SOURCES['constants.py'], /"Application Support"\s*\/\s*"([^"]+)"/, 'constants.py'))
  dirs.add(extractMatch(SOURCES['paths.cjs'], /'Application Support',\s*'([^']+)'/, 'paths.cjs'))
  dirs.add(extractMatch(SOURCES['paths.rs'], /"Library\/Application Support\/([^"]+)"/, 'paths.rs'))
  dirs.add(extractMatch(SOURCES['install.sh'], /Library\/Application Support\/([^"]+)"/, 'install.sh'))
  assert.strictEqual(dirs.size, 1, `macOS dir name drift: ${[...dirs].join(' vs ')}`)
})
