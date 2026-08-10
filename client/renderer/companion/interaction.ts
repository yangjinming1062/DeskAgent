import type { ReactionBucket } from '@/shared/types/reactions'

import { resolveInteractionClip } from './3d/clip-dispatch'
import { getClipDefs } from './3d/clips-registry'
import { $availableClipNames, $modelInfo } from './3d/model-store'
import { reportInteractionStat } from './activity'
import { $clipOverride, setSpriteState } from './companion-store'
import { $personalityTags } from './persona-store'
import { pickReaction, playReactionAudio } from './reactions/reaction-audio'

let lastPokeTime = 0
let pokeCount = 0
let resetTimer: ReturnType<typeof setTimeout> | null = null
let hoverThrottleTimer: ReturnType<typeof setTimeout> | null = null

function bucketForPokeCount(): ReactionBucket {
  if (pokeCount >= 5) {
    return 'poke-heavy'
  }

  if (pokeCount >= 3) {
    return 'poke-medium'
  }

  return 'poke-light'
}

export function handlePokeInteraction(): void {
  const now = Date.now()

  if (now - lastPokeTime < 3000) {
    pokeCount += 1
  } else {
    pokeCount = 1
  }

  lastPokeTime = now

  if (resetTimer) {
    clearTimeout(resetTimer)
  }

  resetTimer = setTimeout(() => {
    pokeCount = 0
  }, 4000)

  const tags = $personalityTags.get()
  const library = getClipDefs($modelInfo.get().rig_type)
  const available = $availableClipNames.get()
  const bucket = bucketForPokeCount()

  const clip = resolveInteractionClip(bucket, tags, library, available)
  $clipOverride.set(clip)
  setSpriteState('interacting', { durationMs: 2000 })

  const entry = pickReaction(bucket, tags)

  void playReactionAudio(entry)
  reportInteractionStat('poke')
}

export function handleHoverInteraction(): void {
  if (hoverThrottleTimer) {
    return
  }

  setSpriteState('interacting', { durationMs: 1500 })
  hoverThrottleTimer = setTimeout(() => {
    hoverThrottleTimer = null
  }, 10000)
}

export function handleDragEndInteraction(): void {
  const tags = $personalityTags.get()
  const library = getClipDefs($modelInfo.get().rig_type)
  const available = $availableClipNames.get()

  const clip = resolveInteractionClip('drag', tags, library, available)
  $clipOverride.set(clip)
  setSpriteState('interacting', { durationMs: 2000 })

  const entry = pickReaction('drag', tags)

  void playReactionAudio(entry)
  reportInteractionStat('drag')
}
