import { describe, expect, it } from 'vitest'

import { getSeedTagsForRig } from './personality-tags'

describe('personality-tags', () => {
  it('getSeedTagsForRig returns common + rig specific seeds', () => {
    const avianSeeds = getSeedTagsForRig('avian')
    expect(avianSeeds).toContain('高傲')
    expect(avianSeeds).toContain('温顺') // from common
    expect(avianSeeds).toContain('翱翔')
  })
})
