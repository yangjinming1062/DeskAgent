import type { ReactionBucket } from '@/shared/types/reactions'

import { reportInteractionStat } from './activity'
import { setSpriteState } from './companion-store'
import { personaTone } from './persona-store'
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

  setSpriteState('interacting', { durationMs: 2000 })

  const tone = personaTone()
  const bucket = bucketForPokeCount()
  const entry = pickReaction(bucket, tone)

  void playReactionAudio(entry, { tone, bucket, userInitiated: true })
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
  setSpriteState('interacting', { durationMs: 2000 })

  const tone = personaTone()
  const entry = pickReaction('drag', tone)

  void playReactionAudio(entry, { tone, bucket: 'drag', userInitiated: true })
  reportInteractionStat('drag')
}
