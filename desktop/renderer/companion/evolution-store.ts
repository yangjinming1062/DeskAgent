import { atom } from 'nanostores'

import { setSpriteState } from './companion-store'
import { speakProactive } from './proactive/proactive'

export const $intimacyScore = atom<number>(0)
export const $evolutionLevel = atom<number>(1)
export const $totalInteractions = atom<number>(0)

function calculateLevel(score: number): number {
  if (score >= 300) {return 5}

  if (score >= 120) {return 4}

  if (score >= 50) {return 3}

  if (score >= 15) {return 2}

  return 1
}

export function recordInteraction(type: 'chat' | 'poke' | 'voice'): void {
  const currentTotal = $totalInteractions.get() + 1
  $totalInteractions.set(currentTotal)

  const points = type === 'voice' ? 3 : 1
  const currentScore = $intimacyScore.get() + points
  $intimacyScore.set(currentScore)

  const currentLevel = $evolutionLevel.get()
  const newLevel = calculateLevel(currentScore)

  if (newLevel > currentLevel) {
    $evolutionLevel.set(newLevel)
    setSpriteState('emotional', { emotion: 'excited', durationMs: 3000 })
    void speakProactive(`我们之间的亲密度升级啦！现在是 Level ${newLevel} 啦！✨`)
  }
}
