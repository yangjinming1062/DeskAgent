import { describe, expect, it } from 'vitest'

import { assemblePersona, deriveSpeakingStyle } from './persona'

describe('assemblePersona', () => {
  it('maps name/personality and derives speaking_style', () => {
    const p = assemblePersona({ name: '小光', personality: '温柔体贴', role: '专属管家' })
    expect(p).toEqual({ name: '小光', personality: '温柔体贴', speaking_style: '专业干练', background: '专属管家' })
  })

  it('maps species / character_gender to biological_type / gender', () => {
    const p = assemblePersona({ name: '梦鳞', species: '灵兽', character_gender: '女', appearance: '金发' })
    expect(p.biological_type).toBe('灵兽')
    expect(p.gender).toBe('女')
    expect(p.appearance).toBe('金发')
  })

  it('passes user_* fields through verbatim (backend routes them to memory)', () => {
    const p = assemblePersona({
      name: '梦鳞',
      personality: '温柔',
      user_call_name: '老板',
      user_hobbies: '音乐',
      user_freeform: '早起型',
    })

    expect(p.user_call_name).toBe('老板')
    expect(p.user_hobbies).toBe('音乐')
    expect(p.user_freeform).toBe('早起型')
  })

  it('drops empty user_* keys so PUT body stays clean', () => {
    const p = assemblePersona({ name: '小光', user_call_name: '', user_hobbies: '   ' })
    expect(p).not.toHaveProperty('user_call_name')
    expect(p).not.toHaveProperty('user_hobbies')
  })

  it('truncates appearance to 500 chars (matches schema max_length)', () => {
    const long = '金'.repeat(800)
    const p = assemblePersona({ name: '小光', appearance: long })
    expect(p.appearance?.length).toBe(500)
  })

  it('truncates user_* free-text fields to 2000 chars (matches schema max_length)', () => {
    const long = '音'.repeat(4000)
    const p = assemblePersona({ name: '小光', user_freeform: long, user_hobbies: long })
    expect(p.user_freeform?.length).toBe(2000)
    expect(p.user_hobbies?.length).toBe(2000)
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

  it('never leaks voice into the persona (TTS layer, not persona; user_* go to memory, not persona either)', () => {
    // voice is consumed by tts.match_voice, not assembled into the persona PUT.
    const p = assemblePersona({ name: '小光', voice: '少女音' })
    expect(p).not.toHaveProperty('voice')
    // Backend PersonaUpdate is extra="forbid" — without role / species / character_gender /
    // appearance / user_* there's nothing extra in the payload.
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

  // 1-6 (backend audit): user-picked speaking_style from onboarding
  // Q13 wins over the personality-key derivation so the persona is
  // a direct reflection of the user's actual selection.
  it('user-picked speaking_style overrides personality-key derivation', () => {
    const p = assemblePersona({
      name: '小光',
      personality: '毒舌傲娇',
      speaking_style: '专业干练',
    })
    expect(p.speaking_style).toBe('专业干练')
  })

  it('falls back to personality-key derivation when user skipped speaking_style', () => {
    const p = assemblePersona({ name: '小光', personality: '毒舌傲娇' })
    expect(p.speaking_style).toBe('俏皮带点小傲娇')
  })
})
