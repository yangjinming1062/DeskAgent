'use strict'

const path = require('node:path')
const fs = require('node:fs')
const { fileURLToPath } = require('node:url')

// Always-hidden noise (covers non-git projects too — gitignore would catch
// these anyway when present, but we want the same hygiene without one).
const FS_READDIR_HIDDEN = new Set([
  '.git',
  '.hg',
  '.svn',
  '.cache',
  '.next',
  '.turbo',
  '.venv',
  '__pycache__',
  'build',
  'dist',
  'node_modules',
  'target',
  'venv'
])

// Match the desktop CompletionEntry shape consumed by use-at-completions.ts
// and use-context-suggestions.ts: {label, value?, isDirectory?}.
const FS_COMPLETE_LIMIT = 50

async function findGitRoot(start) {
  let dir = start

  for (let i = 0; i < 50; i += 1) {
    try {
      await fs.promises.access(path.join(dir, '.git'))
      return dir
    } catch {
      // not a git root — walk up
    }

    const parent = path.dirname(dir)

    if (parent === dir) {
      return null
    }

    dir = parent
  }

  return null
}

async function readBranchFromHead(gitRoot) {
  // Avoid spawning ``git`` — depends on PATH and on Windows is fragile.
  // .git/HEAD is either ``ref: refs/heads/<branch>`` (normal repo) or a
  // 40-char SHA (detached HEAD). Return the branch string for the normal case,
  // empty string for detached / missing / malformed.
  if (!gitRoot) {
    return ''
  }
  let raw
  try {
    raw = await fs.promises.readFile(path.join(gitRoot, '.git', 'HEAD'), 'utf8')
  } catch {
    return ''
  }
  const trimmed = raw.trim()
  const prefix = 'ref: refs/heads/'
  if (trimmed.startsWith(prefix)) {
    return trimmed.slice(prefix.length).trim()
  }
  // detached HEAD — return short SHA so the UI can still show *something*
  return trimmed.slice(0, 7)
}

async function listDirents(dir, { prefix, limit } = {}) {
  // Shared dirent walker used by readDir and completePath. Reads ``dir``,
  // drops FS_READDIR_HIDDEN entries, sorts directories first then by name,
  // and returns the trimmed {name, path, isDirectory} list. ``prefix`` is a
  // case-insensitive filter on entry names; ``limit`` caps the result.
  let dirents
  try {
    dirents = await fs.promises.readdir(dir, { withFileTypes: true })
  } catch (error) {
    return { entries: [], error: error?.code || 'read-error' }
  }
  const lowerPrefix = (prefix || '').toLowerCase()
  const filtered = dirents
    .filter(d => !FS_READDIR_HIDDEN.has(d.name))
    .filter(d => !lowerPrefix || d.name.toLowerCase().startsWith(lowerPrefix))
    .map(d => ({ name: d.name, path: path.join(dir, d.name), isDirectory: d.isDirectory() }))
    .sort((a, b) => Number(b.isDirectory) - Number(a.isDirectory) || a.name.localeCompare(b.name))
  return { entries: limit ? filtered.slice(0, limit) : filtered }
}

function registerFsIpc({ ipcMain }) {
  // Normalize a user-supplied path (file:// URL, absolute, or relative) to
  // an absolute path on disk, and (when it points to a file) walk up to the
  // containing directory. Returns the directory to start a git lookup from.
  async function resolveStartDir(startPath) {
    const input = String(startPath || '')
    const resolved = input.startsWith('file:') ? fileURLToPath(input) : path.resolve(input)
    try {
      const stat = await fs.promises.stat(resolved)
      return stat.isDirectory() ? resolved : path.dirname(resolved)
    } catch {
      // Path may not exist (e.g. inside a just-removed repo) — git lookup
      // handles a non-existent starting dir fine; pass through the resolved
      // value either way.
      return resolved
    }
  }

  ipcMain.handle('zast:fs:readDir', async (_event, dirPath) => {
    const resolved = path.resolve(String(dirPath || ''))
    if (!resolved) return { entries: [], error: 'invalid-path' }
    return listDirents(resolved)
  })

  ipcMain.handle('zast:fs:gitRoot', async (_event, startPath) => {
    return findGitRoot(await resolveStartDir(startPath))
  })

  // Branch extraction by reading .git/HEAD directly. The backend can't do
  // this (Docker has no access to user disk) and we don't want to depend on
  // a ``git`` binary in PATH, so the desktop owns this lookup.
  ipcMain.handle('zast:fs:gitBranch', async (_event, startPath) => {
    const start = await resolveStartDir(startPath)
    const root = await findGitRoot(start)
    const branch = await readBranchFromHead(root)
    return { branch, root }
  })

  // Path completion for the @-mention composer. Accepts ``{word, cwd}`` where
  // ``word`` may be ``"@file:"`` (suggestion panel) or a partial path the
  // user is typing (``"@src/ut"``). Always returns at most FS_COMPLETE_LIMIT
  // entries, directories first.
  ipcMain.handle('zast:fs:completePath', async (_event, params) => {
    const word = String(params?.word ?? '')
    const cwdInput = String(params?.cwd ?? '')
    const cwd = cwdInput ? path.resolve(cwdInput) : process.cwd()

    // Strip a leading "@" + optional namespace (e.g. "file:") so we get a
    // real relative path to complete. If word is exactly "@file:" with no
    // tail, we list cwd; otherwise we split into base + prefix.
    let remainder = word
    if (remainder.startsWith('@')) {
      remainder = remainder.slice(1)
    }
    const colonIdx = remainder.indexOf(':')
    if (colonIdx >= 0) {
      remainder = remainder.slice(colonIdx + 1)
    }
    remainder = remainder.replace(/^\/+/, '')

    const lastSlash = remainder.lastIndexOf('/')
    const baseDir = lastSlash >= 0 ? remainder.slice(0, lastSlash) : ''
    const prefix = lastSlash >= 0 ? remainder.slice(lastSlash + 1) : remainder

    const targetDir = baseDir ? path.resolve(cwd, baseDir) : cwd
    const { entries } = await listDirents(targetDir, { prefix, limit: FS_COMPLETE_LIMIT })
    const items = entries.map(({ name, path: fullPath, isDirectory }) => ({
      label: `${baseDir ? baseDir + '/' : ''}${name}${isDirectory ? '/' : ''}`,
      value: fullPath,
      isDirectory
    }))
    return { items }
  })
}

module.exports = { registerFsIpc, findGitRoot, readBranchFromHead, FS_READDIR_HIDDEN }
