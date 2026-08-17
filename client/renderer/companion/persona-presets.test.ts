import { describe, expect, it } from 'vitest'

import {
  APPEARANCE_PRESETS,
  CHARACTER_GENDER_PRESETS,
  PERSONALITY_PRESETS,
  ROLE_PRESETS,
  SPEAKING_STYLE_PRESETS,
  SPECIES_PRESETS,
  USER_AGE_BUCKET_PRESETS,
  USER_GENDER_PRESETS,
  VOICE_PRESETS
} from './persona-presets'

const ALL_PRESETS = {
  APPEARANCE_PRESETS,
  CHARACTER_GENDER_PRESETS,
  PERSONALITY_PRESETS,
  ROLE_PRESETS,
  SPECIES_PRESETS,
  SPEAKING_STYLE_PRESETS,
  USER_AGE_BUCKET_PRESETS,
  USER_GENDER_PRESETS,
  VOICE_PRESETS
} as const

describe('persona-presets', () => {
  it('exposes non-empty chips for every dimension', () => {
    for (const [name, list] of Object.entries(ALL_PRESETS)) {
      expect(list.length, `${name} should have at least one preset`).toBeGreaterThan(0)
    }
  })

  it('does not contain duplicate entries within a single dimension', () => {
    for (const [name, list] of Object.entries(ALL_PRESETS)) {
      expect(new Set(list).size, `${name} should not have duplicates`).toBe(list.length)
    }
  })

  it('keeps the canonical role / personality / species chip sets', () => {
    // These three are user-visible anchors of the persona product. Changing
    // them is a product decision, not a refactor — encode the contract here
    // so a stray rewrite trips CI.
    expect([...ROLE_PRESETS]).toEqual(['亲密的爱人', '灵魂伴侣', '赛博管家', '知己好友', '宠物', '伙伴'])
    expect([...PERSONALITY_PRESETS]).toEqual([
      '温柔体贴',
      '活泼好动',
      '阳光开朗',
      '优雅知性',
      '冷静理性',
      '毒舌傲娇',
      '腹黑呆萌',
      '高冷清冷'
    ])
    expect([...SPECIES_PRESETS]).toEqual(['人类', '灵兽', '精灵', '机甲', '幻形'])
  })
})
