import { describe, expect, it } from 'vitest'

import { getSeedTagsForRig, PERSONALITY_TAG_SEED, PERSONALITY_TAG_SEED_BY_RIG } from './personality-tags'

describe('personality-tags', () => {
  it('defines non-empty seeds for all 7 rig types + common', () => {
    for (const rig of [
      'common',
      'biped',
      'quadruped',
      'avian',
      'serpentine',
      'aquatic',
      'hexapod',
      'octopod'
    ] as const) {
      expect(PERSONALITY_TAG_SEED_BY_RIG[rig].length, `${rig} seed list is empty`).toBeGreaterThan(0)
    }
  })

  it('PERSONALITY_TAG_SEED combines common + all rigs without duplicates', () => {
    const set = new Set(PERSONALITY_TAG_SEED)
    expect(set.size).toBe(PERSONALITY_TAG_SEED.length)
  })

  it('getSeedTagsForRig returns common + rig specific seeds', () => {
    const avianSeeds = getSeedTagsForRig('avian')
    expect(avianSeeds).toContain('高傲')
    expect(avianSeeds).toContain('温顺') // from common
    expect(avianSeeds).toContain('翱翔')
  })
})
