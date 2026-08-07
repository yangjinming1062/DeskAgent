import { describe, expect, it } from 'vitest'

import { safeJsonParse } from './safe-json'

describe('safeJsonParse', () => {
  it('returns the fallback for empty or non-string input', () => {
    expect(safeJsonParse(null, { x: 1 })).toEqual({ x: 1 })
    expect(safeJsonParse(undefined, { x: 1 })).toEqual({ x: 1 })
    expect(safeJsonParse('', { x: 1 })).toEqual({ x: 1 })
  })

  it('parses valid JSON', () => {
    expect(safeJsonParse('{"x":2}', { x: 1 })).toEqual({ x: 2 })
  })

  it('returns the fallback for malformed JSON', () => {
    expect(safeJsonParse('{not json', { x: 1 })).toEqual({ x: 1 })
  })

  it('returns the fallback for the JSON literal null', () => {
    expect(safeJsonParse('null', { x: 1 })).toEqual({ x: 1 })
  })
})
