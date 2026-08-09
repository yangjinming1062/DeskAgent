import { describe, expect, it } from 'vitest'

import { getSeedTagsForRig, PERSONALITY_TAG_SEED, PERSONALITY_TAG_SEED_BY_RIG } from './personality-tags'

describe('personality-tags', () => {
  it('defines seeds for all 7 rig types + common', () => {
    expect(PERSONALITY_TAG_SEED_BY_RIG.common.length).toBeGreaterThan(10)
    expect(PERSONALITY_TAG_SEED_BY_RIG.biped.length).toBeGreaterThan(30)
    expect(PERSONALITY_TAG_SEED_BY_RIG.quadruped.length).toBeGreaterThan(10)
    expect(PERSONALITY_TAG_SEED_BY_RIG.avian.length).toBeGreaterThan(10)
    expect(PERSONALITY_TAG_SEED_BY_RIG.serpentine.length).toBeGreaterThan(10)
    expect(PERSONALITY_TAG_SEED_BY_RIG.aquatic.length).toBeGreaterThan(10)
    expect(PERSONALITY_TAG_SEED_BY_RIG.hexapod.length).toBeGreaterThan(10)
    expect(PERSONALITY_TAG_SEED_BY_RIG.octopod.length).toBeGreaterThan(10)
  })

  it('PERSONALITY_TAG_SEED combines common + all rigs without duplicates', () => {
    expect(PERSONALITY_TAG_SEED.length).toBeGreaterThan(100)
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
