import { describe, expect, it } from 'vitest'

import { BUILTIN_EMOTIONS } from '@/companion/companion-store'

import { BUILTIN_VALENCE, resolveEmotionClip, resolveInteractionClip, selectClipByTags } from './clip-dispatch'
import type { ClipDef } from './clips-biped'
import type { CompanionExpression } from './model-store'

const mockClips: ClipDef[] = [
  {
    name: 'poke_gentle',
    duration: 1.5,
    loop: false,
    category: 'interaction',
    tags: ['温柔', '体贴'],
    tracks: {}
  },
  {
    name: 'poke_lively',
    duration: 1.5,
    loop: false,
    category: 'interaction',
    tags: ['活泼', '元气'],
    tracks: {}
  },
  {
    name: 'poke_snarky',
    duration: 1.5,
    loop: false,
    category: 'interaction',
    tags: ['傲娇', '毒舌'],
    tracks: {}
  },
  {
    name: 'drag_land_gentle',
    duration: 1.2,
    loop: false,
    category: 'locomotion',
    tags: ['温柔'],
    tracks: {}
  },
  {
    name: 'happy_bounce',
    duration: 1.5,
    loop: false,
    category: 'emotion-positive',
    tags: ['活泼', '元气'],
    tracks: {}
  },
  {
    name: 'shy_smile',
    duration: 1.5,
    loop: false,
    category: 'emotion-positive',
    tags: ['害羞', '温柔'],
    tracks: {}
  }
]

const mockLibrary: Record<string, ClipDef> = Object.fromEntries(mockClips.map(c => [c.name, c]))
const allAvailable = new Set(mockClips.map(c => c.name))

describe('clip-dispatch', () => {
  describe('selectClipByTags', () => {
    it('picks clip with highest tag intersection score', () => {
      const selected = selectClipByTags(mockClips, ['活泼', '元气', '开朗'], allAvailable)
      expect(['poke_lively', 'happy_bounce']).toContain(selected)
    })

    it('falls back to candidate pool if no tags match', () => {
      const selected = selectClipByTags(mockClips, ['神秘', '未知'], allAvailable)
      expect(selected).toBeTruthy()
      expect(mockClips.some(c => c.name === selected)).toBe(true)
    })

    it('respects available set constraint', () => {
      const onlyGentle = new Set(['poke_gentle'])
      const selected = selectClipByTags(mockClips, ['活泼'], onlyGentle)
      expect(selected).toBe('poke_gentle')
    })

    it('returns null when candidates list is empty or none available', () => {
      const selected = selectClipByTags([], ['温柔'], allAvailable)
      expect(selected).toBeNull()
    })
  })

  describe('resolveInteractionClip', () => {
    it('resolves poke clip matching companion personality tags', () => {
      const clip = resolveInteractionClip('poke-light', ['傲娇'], mockLibrary, allAvailable)
      expect(clip).toBe('poke_snarky')
    })

    it('resolves drag clip for drag bucket', () => {
      const clip = resolveInteractionClip('drag', ['温柔'], mockLibrary, allAvailable)
      expect(clip).toBe('drag_land_gentle')
    })
  })

  describe('resolveEmotionClip', () => {
    it('resolves positive emotion clip matching companion tags', () => {
      const clip = resolveEmotionClip('happy', ['害羞'], mockLibrary, allAvailable)
      expect(clip).toBe('shy_smile')
    })

    it('resolves surprise/confused/sleepy emotions gracefully', () => {
      const surpriseClip = {
        name: 'surprise_jump',
        duration: 1.0,
        loop: false,
        category: 'surprise' as const,
        tags: ['敏锐'],
        tracks: {}
      }

      const lib = { ...mockLibrary, surprise_jump: surpriseClip }
      const avail = new Set([...allAvailable, 'surprise_jump'])

      const clip = resolveEmotionClip('surprised', ['敏锐'], lib, avail)
      expect(clip).toBe('surprise_jump')

      const confusedClip = resolveEmotionClip('confused', [], lib, avail)
      expect(confusedClip).toBe('surprise_jump')
    })
  })

  describe('BUILTIN_VALENCE', () => {
    it('classifies every built-in emotion', () => {
      // Every built-in emotion must have a valence entry — a missing key
      // would silently default to 'neutral' and mis-route clip selection.
      for (const e of BUILTIN_EMOTIONS) {
        expect(BUILTIN_VALENCE[e], `missing valence for '${e}'`).toBeDefined()
      }
    })

    it('maps positive/negative/neutral correctly', () => {
      expect(BUILTIN_VALENCE.happy).toBe('positive')
      expect(BUILTIN_VALENCE.sad).toBe('negative')
      expect(BUILTIN_VALENCE.neutral).toBe('neutral')
      expect(BUILTIN_VALENCE.sleepy).toBe('neutral')
    })
  })

  describe('resolveEmotionClip with custom expressions', () => {
    const tagMatchedClip: ClipDef = {
      name: 'tender_worry_pose',
      duration: 2.0,
      loop: false,
      category: 'emotion-negative',
      tags: ['温柔', '心疼'],
      tracks: {}
    }

    const lib = { ...mockLibrary, tender_worry_pose: tagMatchedClip }
    const avail = new Set([...allAvailable, 'tender_worry_pose'])

    const customExpr: CompanionExpression = {
      id: 1,
      // Mixed case on purpose — the dispatch index must normalize both sides.
      name: 'Tender_Worry',
      label: '心疼',
      valence: 'negative',
      description: 'Tender worry',
      weights: { frown: 0.5 },
      tags: ['温柔', '心疼'],
      scale_boost: 1.0
    }

    it('uses custom expression valence as fallback for unknown emotion', () => {
      // 'tender_worry' is not in BUILTIN_VALENCE, so the custom expression's
      // valence='negative' should drive candidate selection.
      const clip = resolveEmotionClip('tender_worry', [], lib, avail, [customExpr])
      expect(clip).toBe('tender_worry_pose')
    })

    it('matches clips by custom expression tags', () => {
      // The custom expression has tags ['温柔','心疼'] which match
      // tender_worry_pose; combined with the valence bucket, the tag-matched
      // clip should be a strong candidate.
      const clip = resolveEmotionClip('tender_worry', ['活泼'], lib, avail, [customExpr])
      expect(clip).toBe('tender_worry_pose')
    })

    it('is case-insensitive on custom expression name', () => {
      const clip = resolveEmotionClip('TENDER_WORRY', [], lib, avail, [customExpr])
      expect(clip).toBe('tender_worry_pose')
    })

    it('falls back to BUILTIN_VALENCE when custom expression is absent', () => {
      const clip = resolveEmotionClip('happy', ['害羞'], mockLibrary, allAvailable, [])
      expect(clip).toBe('shy_smile')
    })
  })
})
