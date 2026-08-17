import { describe, expect, it, vi } from 'vitest'

import { pickReaction, playReactionAudio } from './reaction-audio'

const hoisted = vi.hoisted(() => ({ speakScripted: vi.fn().mockResolvedValue(true) }))

vi.mock('../tts', () => ({ speakScripted: hoisted.speakScripted }))

describe('playReactionAudio', () => {
  it('speaks the manifest line through the persisted-TTS path', async () => {
    hoisted.speakScripted.mockClear()
    const entry = pickReaction('poke-light', ['温柔'])

    expect(entry).not.toBeNull()
    await expect(playReactionAudio(entry)).resolves.toBe(true)
    // `persist: true` lives inside speakScripted — first poke synthesises and
    // writes the clip, every later poke reads it back off disk.
    expect(hoisted.speakScripted).toHaveBeenCalledWith(entry?.text, undefined, 'reaction')
  })

  it('stays silent when the manifest yields no entry', async () => {
    hoisted.speakScripted.mockClear()

    await expect(playReactionAudio(null)).resolves.toBe(false)
    expect(hoisted.speakScripted).not.toHaveBeenCalled()
  })
})

describe('pickReaction', () => {
  it('prefers entries whose tags intersect the companion personality', () => {
    const entry = pickReaction('poke-light', ['傲娇', '毒舌'])

    expect(entry?.tags.some(t => ['傲娇', '毒舌'].includes(t))).toBe(true)
  })

  it('allows generic tag-free entries to participate in rotation when tags match', () => {
    // Run multiple picks on drag bucket with matching tags to observe candidate eligibility
    const pickedTags: number[] = []

    for (let i = 0; i < 50; i++) {
      const entry = pickReaction('drag', ['温柔'])

      if (entry) {
        pickedTags.push(entry.tags.length)
      }
    }

    // Eligible entries are gentle (tags.length = 3) and generic (tags.length = 0)
    expect(pickedTags.every(len => len === 3 || len === 0)).toBe(true)
    expect(pickedTags.some(len => len === 0)).toBe(true)
  })

  it('still returns a same-bucket entry when no tag matches', () => {
    const entry = pickReaction('drag', ['不存在的标签'])

    expect(entry?.bucket).toBe('drag')
  })

  it('returns null for a bucket with no candidates', () => {
    expect(pickReaction('not-a-bucket' as never, [])).toBeNull()
  })
})
