import { describe, expect, it } from 'vitest'

// Runtime P1-4: the installer (Rust) writes the bootstrap file; the
// desktop (JS) reads it. The two sides agree on three constants:
//   1. filename        "agent-session-bootstrap.json"
//   2. consumed suffix ".consumed"
//   3. schema version 1
// A drift between the two silently breaks the install -> first-launch
// handoff (user gets a 'login again' instead of a transparent
// continuation). This test pins the JS-side values; the Rust
// constants live in installer/src-tauri/src/paths.rs and must match.
//
// The test reads the Rust file at module load (paths.rs is the source
// of truth) so any drift here is caught at vitest time, not at
// runtime.

const RUST_PATHS_RS_REL = '../../../installer/src-tauri/src/paths.rs'

async function loadRustConstants(): Promise<{ filename: string; consumedSuffix: string; schemaVersion: number }> {
  const fs = await import('node:fs/promises')
  const path = await import('node:path')
  // Resolve relative to this test file's location.
  const rustFile = path.resolve(__dirname, RUST_PATHS_RS_REL)
  const src = await fs.readFile(rustFile, 'utf8')

  const filenameMatch = src.match(/BOOTSTRAP_FILENAME:\s*&str\s*=\s*"([^"]+)"/)
  const consumedMatch = src.match(/BOOTSTRAP_CONSUMED_SUFFIX:\s*&str\s*=\s*"([^"]+)"/)
  const schemaMatch = src.match(/BOOTSTRAP_SCHEMA_VERSION:\s*u32\s*=\s*(\d+)/)

  if (!filenameMatch || !consumedMatch || !schemaMatch) {
    throw new Error(`Could not parse bootstrap constants from ${rustFile}`)
  }

  return {
    filename: filenameMatch[1],
    consumedSuffix: consumedMatch[1],
    schemaVersion: Number(schemaMatch[1]),
  }
}

describe('cross-language bootstrap constants', () => {
  it('JS-side constants match installer/src-tauri/src/paths.rs', async () => {
    // These mirror the constants in desktop/main/backend/bootstrap-session.cjs.
    // If the test fails, update both sides together.
    const JS_FILENAME = 'agent-session-bootstrap.json'
    const JS_CONSUMED_SUFFIX = '.consumed'
    const JS_SCHEMA_VERSION = 1

    const rust = await loadRustConstants()

    expect(JS_FILENAME).toBe(rust.filename)
    expect(JS_CONSUMED_SUFFIX).toBe(rust.consumedSuffix)
    expect(JS_SCHEMA_VERSION).toBe(rust.schemaVersion)
  })
})
