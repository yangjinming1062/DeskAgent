'use strict'

const fs = require('fs')
const path = require('path')

// Pure check — returns { ok: true } or { ok: false, error: "..." }.
// Kept side-effect-free so it can be unit tested without spawning a process.
function checkDistBuilt(distDir) {
  if (!fs.existsSync(distDir) || !fs.statSync(distDir).isDirectory()) {
    return { ok: false, error: `no dist directory at ${distDir}` }
  }

  const indexHtml = path.join(distDir, 'index.html')
  if (!fs.existsSync(indexHtml) || !fs.statSync(indexHtml).isFile()) {
    return { ok: false, error: `dist/index.html is missing at ${indexHtml}` }
  }
  if (fs.statSync(indexHtml).size === 0) {
    return { ok: false, error: `dist/index.html is empty at ${indexHtml}` }
  }

  // index.html alone isn't enough — vite emits hashed JS into dist/assets.
  // An index.html with no script bundle still blank-pages.
  const assetsDir = path.join(distDir, 'assets')
  const hasAssets =
    fs.existsSync(assetsDir) &&
    fs.statSync(assetsDir).isDirectory() &&
    fs.readdirSync(assetsDir).some(name => name.endsWith('.js'))
  if (!hasAssets) {
    return { ok: false, error: `dist/assets has no built JS bundle (expected vite output under ${assetsDir})` }
  }

  return { ok: true }
}

function main() {
  const desktopRoot = path.resolve(__dirname, '..')
  const distDir = path.join(desktopRoot, 'dist')
  const result = checkDistBuilt(distDir)

  if (!result.ok) {
    console.error(`\n✗ assert-dist-built: ${result.error}`)
    console.error('  The renderer bundle is missing or incomplete, so packaging')
    console.error('  would produce an app that launches to a blank page.')
    console.error('  Re-run the build and check the tsc/vite output above for the')
    console.error('  real failure, then package again:')
    console.error(`    cd ${desktopRoot} && pnpm run build\n`)
    process.exit(1)
  }

  console.log('✓ assert-dist-built: dist/index.html + assets present')
}

if (require.main === module) {
  main()
}

module.exports = { checkDistBuilt }
