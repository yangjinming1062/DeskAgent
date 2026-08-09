import type { SpriteEmotion } from '@/companion/companion-store'
import type { ReactionBucket } from '@/shared/types/reactions'

import type { ClipDef } from './clips-biped'

/** 按 tags 交集匹配从候选 clip 中选择动作名。
 *  交集匹配 > 0 优先；通用/无专属标签 clip 始终作为候选池的一部分。
 *  有多个最高匹配候选时随机选择以增加动作多样性。 */
export function selectClipByTags(
  candidates: readonly ClipDef[],
  companionTags: string[],
  available: Set<string>
): string | null {
  const activeCandidates = candidates.filter(c => available.size === 0 || available.has(c.name))

  if (activeCandidates.length === 0) {
    return null
  }

  if (companionTags.length > 0) {
    const tagSet = new Set(companionTags)

    const scored = activeCandidates.map(c => ({
      clip: c,
      score: (c.tags ?? []).filter(t => tagSet.has(t)).length
    }))

    const maxScore = Math.max(...scored.map(s => s.score))

    if (maxScore > 0) {
      const topMatches = scored.filter(s => s.score === maxScore).map(s => s.clip)

      return topMatches[Math.floor(Math.random() * topMatches.length)].name
    }
  }

  return activeCandidates[Math.floor(Math.random() * activeCandidates.length)].name
}

/** 解析 poke / drag 交互动作 clip。 */
export function resolveInteractionClip(
  bucket: ReactionBucket,
  companionTags: string[],
  library: Record<string, ClipDef>,
  available: Set<string>
): string | null {
  const allClips = Object.values(library)

  // 优先筛选 interaction 分类或与交互意图相关的 clip
  let candidates = allClips.filter(c => c.category === 'interaction' || c.category === 'intimate')

  if (bucket === 'drag') {
    const dragSpecific = allClips.filter(
      c => c.name.includes('drag') || c.name.includes('land') || c.name.includes('drop')
    )

    if (dragSpecific.length > 0) {
      candidates = dragSpecific
    }
  } else if (bucket === 'poke-heavy') {
    const heavySpecific = candidates.filter(
      c => c.name.includes('heavy') || c.name.includes('angry') || c.name.includes('startle')
    )

    if (heavySpecific.length > 0) {
      candidates = heavySpecific
    }
  }

  if (candidates.length === 0) {
    candidates = allClips.filter(c => c.name === 'interacting' || c.category === 'state')
  }

  return selectClipByTags(candidates, companionTags, available)
}

const POSITIVE_EMOTIONS: ReadonlySet<SpriteEmotion> = new Set([
  'happy',
  'excited',
  'proud',
  'grateful',
  'playful',
  'curious'
])

const NEGATIVE_EMOTIONS: ReadonlySet<SpriteEmotion> = new Set([
  'sad',
  'concerned',
  'shy',
  'bored',
  'lonely',
  'embarrassed',
  'apologetic'
])

/** 解析情绪动作 clip。 */
export function resolveEmotionClip(
  emotion: SpriteEmotion,
  companionTags: string[],
  library: Record<string, ClipDef>,
  available: Set<string>
): string | null {
  const allClips = Object.values(library)
  let candidates: ClipDef[] = []

  if (POSITIVE_EMOTIONS.has(emotion)) {
    candidates = allClips.filter(c => c.category === 'emotion-positive' || c.name.includes('happy'))
  } else if (NEGATIVE_EMOTIONS.has(emotion)) {
    candidates = allClips.filter(c => c.category === 'emotion-negative' || c.category === 'neg-ext')
  } else if (emotion === 'surprised') {
    candidates = allClips.filter(
      c => c.category === 'surprise' || c.name.includes('surprise') || c.name.includes('shock')
    )
  } else if (emotion === 'confused') {
    candidates = allClips.filter(
      c => c.category === 'surprise' || c.name.includes('curious') || c.name.includes('shrug') || c.name === 'thinking'
    )
  } else if (emotion === 'sleepy') {
    candidates = allClips.filter(
      c => c.name.includes('yawn') || c.name.includes('sleep') || c.name.includes('stretch') || c.category === 'daily'
    )
  }

  if (candidates.length === 0) {
    candidates = allClips.filter(c => c.name === 'emotional_idle' || c.category === 'state')
  }

  return selectClipByTags(candidates, companionTags, available)
}
