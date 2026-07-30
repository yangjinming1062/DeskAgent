import { describe, expect, it } from 'vitest'

import { assemblePersona, deriveSpeakingStyle } from './persona'

describe('assemblePersona', () => {
  it('maps name/personality and derives speaking_style', () => {
    const p = assemblePersona({ name: '小光', personality: '温柔体贴', role: '专属管家' })
    expect(p).toEqual({ name: '小光', personality: '温柔体贴', speaking_style: '专业干练', background: '专属管家' })
  })

  it('defaults skipped required fields so the PUT always satisfies is_complete', () => {
    const p = assemblePersona({ name: '小光' })
    expect(p.name).toBe('小光')
    expect(p.personality).toBe('温柔体贴')
    expect(p.speaking_style).toBe('温柔亲切')
    expect(p.background).toBeUndefined()
  })

  it('falls back to a default name when name is missing', () => {
    expect(assemblePersona({}).name).toBe('伙伴')
  })

  it('never leaks self-intro or voice into the persona (memory/TTS layers, not persona)', () => {
    const p = assemblePersona({ name: '小光', selfIntro: '我是张三', voice: '少女音' })
    expect(p).not.toHaveProperty('selfIntro')
    expect(p).not.toHaveProperty('voice')
    // Backend PersonaUpdate is extra="forbid" — the payload must contain only
    // the schema's keys (no role here → no background either).
    expect(Object.keys(p).sort()).toEqual(['name', 'personality', 'speaking_style'])
  })
})

describe('deriveSpeakingStyle', () => {
  it.each([
    ['毒舌傲娇', '俏皮带点小傲娇'],
    ['冷静理性', '沉稳简洁'],
    ['活泼好动', '轻快活泼'],
    [undefined, '温柔亲切']
  ])('personality %s → %s', (personality, expected) => {
    expect(deriveSpeakingStyle(undefined, personality)).toBe(expected)
  })

  it('leans professional for secretary/steward/jarvis roles', () => {
    expect(deriveSpeakingStyle('专属管家', undefined)).toBe('专业干练')
    expect(deriveSpeakingStyle('贾维斯', undefined)).toBe('专业干练')
  })
})
