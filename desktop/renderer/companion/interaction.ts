import { setSpriteState } from './companion-store'
import { speakProactive } from './proactive/proactive'

let lastPokeTime = 0
let pokeCount = 0
let resetTimer: ReturnType<typeof setTimeout> | null = null

const POKE_REACTIONS_LIGHT = [
  '嗯？怎么啦？',
  '我在呢！',
  '（偷笑）戳了戳我~',
  '有什么事需要我帮忙吗？',
  '要跟我聊天吗？双击我吧！'
]

const POKE_REACTIONS_MEDIUM = [
  '再戳我可要痒发笑了！',
  '太喜欢戳我了吧~',
  '知道啦知道啦！',
  '一直在戳我呢，哼哼~'
]

const POKE_REACTIONS_HEAVY = [
  '别戳啦！脑瓜都要晕啦！',
  '戳够没有呀，哼！',
  '好吧好吧，败给你啦！',
  '再戳我要反击啦！'
]

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

  let pool = POKE_REACTIONS_LIGHT
  if (pokeCount >= 5) {
    pool = POKE_REACTIONS_HEAVY
  } else if (pokeCount >= 3) {
    pool = POKE_REACTIONS_MEDIUM
  }

  const text = pool[Math.floor(Math.random() * pool.length)]
  void speakProactive(text)
}

export function handleHoverInteraction(): void {
  // Gentle interaction on hover
}
