import { describe, expect, it } from 'vitest'

import {
  APPEARANCE_PRESETS,
  type AppearancePreset,
  CHARACTER_GENDER_PRESETS,
  type CharacterGenderPreset,
  PERSONALITY_PRESETS,
  type PersonalityPreset,
  ROLE_PRESETS,
  type RolePreset,
  SPEAKING_STYLE_PRESETS,
  type SpeakingStylePreset,
  SPECIES_PRESETS,
  type SpeciesPreset,
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

  it('keeps per-preset type aliases aligned with the tuple values', () => {
    // Compile-time check: every member of each typed tuple must be a valid
    // instance of its alias. If a developer adds '喜爱' to ROLE_PRESETS
    // without updating RolePreset (or vice versa), this `as RolePreset[]`
    // cast fails and the build breaks.
    const roleTyped: readonly RolePreset[] = ROLE_PRESETS
    const personalityTyped: readonly PersonalityPreset[] = PERSONALITY_PRESETS
    const speciesTyped: readonly SpeciesPreset[] = SPECIES_PRESETS
    const charGenderTyped: readonly CharacterGenderPreset[] = CHARACTER_GENDER_PRESETS
    const appearanceTyped: readonly AppearancePreset[] = APPEARANCE_PRESETS
    const speakingStyleTyped: readonly SpeakingStylePreset[] = SPEAKING_STYLE_PRESETS

    expect(roleTyped).toEqual(ROLE_PRESETS)
    expect(personalityTyped).toEqual(PERSONALITY_PRESETS)
    expect(speciesTyped).toEqual(SPECIES_PRESETS)
    expect(charGenderTyped).toEqual(CHARACTER_GENDER_PRESETS)
    expect(appearanceTyped).toEqual(APPEARANCE_PRESETS)
    expect(speakingStyleTyped).toEqual(SPEAKING_STYLE_PRESETS)
  })
})
