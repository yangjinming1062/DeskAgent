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

  it('allows generic tag-free entries to participate in rotation when tags match', async () => {
    // Minimal manifest: one tag-matched entry plus one generic entry in the
    // same bucket — both must sit in the top-score rotation pool.
    vi.resetModules()
    vi.doMock('./manifest.json', () => ({
      default: {
        language: 'zh',
        format: 'mp3',
        buckets: ['drag'],
        files: [
          { id: 'drag.tagged', tags: ['温柔'], bucket: 'drag', text: 'tagged' },
          { id: 'drag.generic', tags: [], bucket: 'drag', text: 'generic' }
        ]
      }
    }))
    const { pickReaction: pick } = await import('./reaction-audio')
    const random = vi.spyOn(Math, 'random')

    random.mockReturnValue(0)
    expect(pick('drag', ['温柔'])?.id).toBe('drag.tagged')

    random.mockReturnValue(0.999)
    expect(pick('drag', ['温柔'])?.id).toBe('drag.generic')

    random.mockRestore()
  })

  it('still returns a same-bucket entry when no tag matches', () => {
    const entry = pickReaction('drag', ['不存在的标签'])

    expect(entry?.bucket).toBe('drag')
  })

  it('returns null for a bucket with no candidates', () => {
    expect(pickReaction('not-a-bucket' as never, [])).toBeNull()
  })
})
