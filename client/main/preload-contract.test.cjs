'use strict'

// Cross-checks the preload bridge surface (main/preload.cjs) against the
// renderer's TypeScript declarations (renderer/shared/types/global.d.ts) so a
// missing contextBridge.exposeInMainWorld entry fails CI rather than waiting
// for a runtime TypeError in production (the saveClipboardImage class of bug).

const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const PRELOAD_PATH = path.join(__dirname, 'preload.cjs')
const DECL_PATH = path.join(__dirname, '..', 'renderer', 'shared', 'types', 'global.d.ts')

const PRELOAD_SOURCE = fs.readFileSync(PRELOAD_PATH, 'utf8')
const DECL_SOURCE = fs.readFileSync(DECL_PATH, 'utf8')

// Property line at any indent inside the deskagent literal: identifier,
// optional `?`, then `:` followed by a type marker. Lines that match this
// pattern contribute their identifier (group 2) to the property set.
const PROP_LINE_RE = /^(\s*)([A-Za-z_$][\w$]*)\s*(\?)?\s*[:(]/

// Returns the substring of `source` starting immediately after `marker` and
// ending at the matching closing `}`. Throws if the marker or its brace-pair
// cannot be located.
function readBalanced(source, marker) {
  const open = source.indexOf(marker)

  if (open < 0) {
    throw new Error(`marker not found: ${marker}`)
  }

  const bodyStart = open + marker.length
  let depth = 0

  for (let i = open; i < source.length; i++) {
    const ch = source[i]

    if (ch === '{') {
      depth++
    } else if (ch === '}') {
      depth--

      if (depth === 0) {
        return source.slice(bodyStart, i)
      }
    }
  }

  throw new Error(`Unbalanced braces after marker: ${marker}`)
}

// Walk the preload bridge body and collect every property identifier. The
// walker is shallow: it just advances through identifier-followed-by-`:` at
// the head of any entry, including nested `{ ... }` blocks (the walker keeps
// advancing past the closing brace naturally because the `keys.add` path
// re-syncs `i` to the character after the `:`).
function collectPreloadKeys(body) {
  const keys = new Set()
  let i = 0

  while (i < body.length) {
    const ch = body[i]

    // Skip line comments.
    if (ch === '/' && body[i + 1] === '/') {
      const nl = body.indexOf('\n', i)

      if (nl < 0) {
        break
      }

      i = nl + 1
      continue
    }

    // Skip block comments.
    if (ch === '/' && body[i + 1] === '*') {
      const end = body.indexOf('*/', i + 2)

      if (end < 0) {
        break
      }

      i = end + 2
      continue
    }

    // Skip strings.
    if (ch === "'" || ch === '"' || ch === '`') {
      i = skipString(body, i)
      continue
    }

    // Property head: identifier followed by `:` (skip method-shorthand `(`).
    if (isIdentStart(ch)) {
      const start = i

      while (i < body.length && isIdentPart(body[i])) {
        i++
      }

      const name = body.slice(start, i)

      while (i < body.length && /\s/.test(body[i])) {
        i++
      }

      if (body[i] === ':') {
        keys.add(name)
      }

      continue
    }

    i++
  }

  return keys
}

function skipString(body, start) {
  const quote = body[start]
  let i = start + 1

  while (i < body.length) {
    const ch = body[i]

    if (ch === '\\') {
      i += 2
      continue
    }

    if (ch === quote) {
      return i + 1
    }

    i++
  }

  return body.length
}

function isIdentStart(ch) {
  return /[A-Za-z_$]/.test(ch)
}

function isIdentPart(ch) {
  return /[\w$]/.test(ch)
}

// Pull every property identifier from `interface Window { deskagent: {...} }`.
// Properties at the minimum indent are top-level on `deskagent`; nested object
// types (`settings: { ... }`) are indented further and don't match. Lines
// that don't fit the property-line pattern are skipped silently.
function collectDeclKeys() {
  const body = readBalanced(DECL_SOURCE, 'deskagent: {')
  const required = new Set()
  const optional = new Set()

  let minIndent = Infinity

  for (const line of body.split('\n')) {
    if (!line.trim()) {
      continue
    }

    const leading = line.match(/^(\s*)/)[1].length

    if (leading < minIndent) {
      minIndent = leading
    }
  }

  if (!Number.isFinite(minIndent)) {
    return { required, optional }
  }

  for (const line of body.split('\n')) {
    const m = line.match(PROP_LINE_RE)

    if (!m || m[1].length !== minIndent) {
      continue
    }

    const name = m[2]

    if (name === 'deskagent' || name === 'Window') {
      continue
    }

    if (m[3]) {
      optional.add(name)
    } else {
      required.add(name)
    }
  }

  return { required, optional }
}

test('preload.cjs exposes every required property declared in global.d.ts', () => {
  const preloadBody = readBalanced(PRELOAD_SOURCE, "exposeInMainWorld('deskagent', {")
  const exposed = collectPreloadKeys(preloadBody)
  const { required, optional } = collectDeclKeys()

  const missing = [...required].filter(k => !exposed.has(k))

  assert.deepEqual(
    missing,
    [],
    `preload.cjs is missing required keys declared as non-optional in global.d.ts: ${missing.join(', ')}`
  )

  if (optional.size > 0) {
    const exposedOptional = [...optional].filter(k => exposed.has(k))
    const unexposedOptional = [...optional].filter(k => !exposed.has(k))

    console.log(`[contract] optional: exposed=${exposedOptional.length}, unexposed=${unexposedOptional.length}`)
  }
})

test('extractBridgeObject returns a non-trivial bridge body', () => {
  const body = readBalanced(PRELOAD_SOURCE, "exposeInMainWorld('deskagent', {")

  assert.ok(body.length > 1000, 'expected non-trivial bridge body')
  assert.ok(!body.includes('exposeInMainWorld'), 'bridge body should not contain the wrapper')
})
