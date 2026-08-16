import type { SpriteEmotion } from '@/companion/companion-store'
import type { ReactionBucket } from '@/shared/types/reactions'

import type { ClipDef } from './clips-biped'
import type { CompanionExpression } from './model-store'

/** Build a lowercase-keyed index of custom expressions once per call — avoid
 * the K-iteration `.find()` plus K `.toLowerCase()` allocations per dispatch. */
function indexCustomExpressions(
  customExpressions: CompanionExpression[] | undefined
): Map<string, CompanionExpression> {
  const map = new Map<string, CompanionExpression>()

  if (!customExpressions) {
    return map
  }

  for (const e of customExpressions) {
    if (e?.name) {
      map.set(e.name.toLowerCase(), e)
    }
  }

  return map
}

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

export const BUILTIN_VALENCE: Record<string, 'positive' | 'negative' | 'neutral'> = {
  happy: 'positive',
  excited: 'positive',
  proud: 'positive',
  grateful: 'positive',
  playful: 'positive',
  curious: 'positive',
  surprised: 'positive',
  smug: 'positive',
  relieved: 'positive',
  sad: 'negative',
  concerned: 'negative',
  shy: 'negative',
  bored: 'negative',
  lonely: 'negative',
  embarrassed: 'negative',
  apologetic: 'negative',
  confused: 'negative',
  pout: 'negative',
  angry: 'negative',
  scared: 'negative',
  sleepy: 'neutral',
  neutral: 'neutral'
}

/** 解析情绪动作 clip。 */
export function resolveEmotionClip(
  emotion: SpriteEmotion,
  companionTags: string[],
  library: Record<string, ClipDef>,
  available: Set<string>,
  customExpressions?: CompanionExpression[],
  action?: string | null
): string | null {
  const allClips = Object.values(library)

  // A structured [action:...] hint narrows to a specific movement clip; fall
  // back to the emotion valence when no clip name/tag matches the token.
  const normAction = action?.trim().toLowerCase()

  if (normAction) {
    const actionClips = allClips.filter(
      c =>
        (available.size === 0 || available.has(c.name)) &&
        (c.name.toLowerCase().includes(normAction) || (c.tags ?? []).some(t => t.toLowerCase().includes(normAction)))
    )

    if (actionClips.length > 0) {
      return selectClipByTags(actionClips, companionTags, available)
    }
  }

  const normEmotion = emotion.toLowerCase()
  const customIndex = indexCustomExpressions(customExpressions)
  const customExpr = customIndex.get(normEmotion)
  const valence = customExpr?.valence ?? BUILTIN_VALENCE[normEmotion] ?? 'neutral'
  const exprTags = customExpr?.tags ?? []
  const combinedTags = Array.from(new Set([...companionTags, ...exprTags]))

  // 1. 按 valence 筛选候选分类桶
  let categoryCandidates: ClipDef[] = []

  if (valence === 'positive') {
    categoryCandidates = allClips.filter(
      c =>
        c.category === 'emotion-positive' ||
        c.category === 'surprise' ||
        c.name.includes('happy') ||
        c.name.includes('smug')
    )
  } else if (valence === 'negative') {
    categoryCandidates = allClips.filter(
      c =>
        c.category === 'emotion-negative' ||
        c.category === 'neg-ext' ||
        c.category === 'surprise' ||
        c.name.includes('sad') ||
        c.name.includes('angry') ||
        c.name.includes('pout') ||
        c.name.includes('scared')
    )
  } else {
    categoryCandidates = allClips.filter(
      c => c.category === 'daily' || c.category === 'micro' || c.category === 'surprise' || c.name.includes('idle')
    )
  }

  // 2. 查找与表情专属标签交集的 clip
  const tagMatchedClips =
    exprTags.length > 0 ? allClips.filter(c => (c.tags ?? []).some(t => exprTags.includes(t))) : []

  // 3. 合并候选池
  let candidates = Array.from(new Set([...categoryCandidates, ...tagMatchedClips]))

  if (candidates.length === 0) {
    candidates = allClips.filter(c => c.name === 'emotional_idle' || c.category === 'state')
  }

  return selectClipByTags(candidates, combinedTags, available)
}
