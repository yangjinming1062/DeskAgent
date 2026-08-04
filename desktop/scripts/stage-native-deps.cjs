'use strict'

const fs = require('node:fs')
const path = require('node:path')

const APP_ROOT = path.resolve(__dirname, '..')
// Desktop is a self-contained pnpm package (pnpm-workspace.yaml declares
// `packages: ['.']`); node-pty lives directly under APP_ROOT/node_modules,
// not hoisted to a parent. APP_ROOT is therefore the root for resolving
// workspace deps.
const REPO_ROOT = APP_ROOT
const STAGE_ROOT = path.join(APP_ROOT, 'build', 'native-deps')

// The target arch may be overridden by electron-builder via npm_config_arch
// (e.g. `pnpm run dist -- --arm64`); fall back to the build host's arch.
const TARGET_ARCH = process.env.npm_config_arch || process.arch
const TARGET_PLATFORM = process.platform

// Modules to stage. The "from" path is the hoisted location in the workspace
// root; "to" is the layout we want inside build/native-deps/.  The "include"
// globs (relative to "from") select the runtime-essential files.  Anything
// outside the include list is left behind (source, deps/, scripts/, etc.).
const NATIVE_DEPS = [
  {
    from: path.join(REPO_ROOT, 'node_modules', 'node-pty'),
    to: path.join(STAGE_ROOT, 'node-pty'),
    include: [
      'package.json',
      'lib/*.js',
      'lib/**/*.js',
      'build/Release/*.node',
      // Per-arch runtime payload. Explicit file types so we don't ship the
      // ~25 MB of .pdb debug symbols that prebuild-install bundles for
      // Windows crash analysis -- not used at runtime, would just bloat
      // the installer.
      `prebuilds/${TARGET_PLATFORM}-${TARGET_ARCH}/*.node`,
      `prebuilds/${TARGET_PLATFORM}-${TARGET_ARCH}/*.dll`,
      `prebuilds/${TARGET_PLATFORM}-${TARGET_ARCH}/*.exe`,
      `prebuilds/${TARGET_PLATFORM}-${TARGET_ARCH}/spawn-helper`,
      `prebuilds/${TARGET_PLATFORM}-${TARGET_ARCH}/conpty/*`
    ]
  }
]

function rmrf(target) {
  fs.rmSync(target, { recursive: true, force: true })
}

function ensureDir(target) {
  fs.mkdirSync(target, { recursive: true })
}

function walk(root) {
  const results = []
  const stack = [root]
  while (stack.length) {
    const current = stack.pop()
    let entries
    try {
      entries = fs.readdirSync(current, { withFileTypes: true })
    } catch {
      continue
    }
    for (const entry of entries) {
      const full = path.join(current, entry.name)
      if (entry.isDirectory()) {
        stack.push(full)
      } else if (entry.isFile()) {
        results.push(full)
      }
    }
  }
  return results
}

// Match a relative path against simple ** and * glob patterns. Implementation
// is intentionally tiny -- the include lists are small and don't need full
// minimatch support.
function matchGlob(rel, pattern) {
  const r = rel.replace(/\\/g, '/')
  const re = new RegExp(
    '^' +
      pattern
        .replace(/\\/g, '/')
        .replace(/[.+^${}()|[\]\\]/g, '\\$&')
        .replace(/\*\*/g, '__DOUBLE_STAR__')
        .replace(/\*/g, '[^/]*')
        .replace(/__DOUBLE_STAR__/g, '.*') +
      '$'
  )
  return re.test(r)
}

function stageOne(spec) {
  if (!fs.existsSync(spec.from)) {
    throw new Error(
      `stage-native-deps: source missing at ${spec.from}.  Run \`pnpm install\` ` + `at the workspace root first.`
    )
  }
  rmrf(spec.to)
  ensureDir(spec.to)

  const files = walk(spec.from)
  let copied = 0
  for (const abs of files) {
    const rel = path.relative(spec.from, abs)
    const included = spec.include.some(g => matchGlob(rel, g))
    if (!included) continue
    const dest = path.join(spec.to, rel)
    ensureDir(path.dirname(dest))
    fs.copyFileSync(abs, dest)
    // node-pty's darwin spawn-helper and the Windows helper binaries
    // (OpenConsole.exe, winpty-agent.exe) are invoked via posix_spawn /
    // CreateProcess at runtime, so they must remain executable in the
    // staged tree.  fs.copyFileSync preserves source mode on POSIX, but we
    // re-assert +x defensively for the darwin spawn-helper (no extension
    // means a stripped mode would be silently broken at runtime).
    if (path.basename(rel) === 'spawn-helper' && process.platform !== 'win32') {
      try {
        fs.chmodSync(dest, 0o755)
      } catch {
        /* best-effort */
      }
    }
    copied += 1
  }
  console.log(`[stage-native-deps] ${path.relative(APP_ROOT, spec.to)}: ${copied} files`)
}

function main() {
  rmrf(STAGE_ROOT)
  ensureDir(STAGE_ROOT)
  for (const spec of NATIVE_DEPS) {
    stageOne(spec)
  }
}

main()
