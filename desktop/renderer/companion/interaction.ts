import { setSpriteState } from './companion-store'
import { recordInteraction } from './evolution-store'
import { personaTone, type ReactionTone } from './persona-store'
import { speakProactive } from './proactive/proactive'

let lastPokeTime = 0
let pokeCount = 0
let resetTimer: ReturnType<typeof setTimeout> | null = null

// Persona-flavoured reaction pools (plan §4.3): same poke, different
// personality → different tone. Escalation (light→medium→heavy) is layered on
// top by frequency. Full LLM+memory-driven generation is a future enhancement.
const POKE_LIGHT: Record<ReactionTone, readonly string[]> = {
  gentle: ['嗯？怎么啦？', '我在呢～', '（偷笑）戳了戳我~', '有什么事需要我帮忙吗？'],
  lively: ['呀！叫我啦？', '嘿嘿，戳我干嘛~', '在在在！怎么啦？', '戳到啦，有什么好玩的事？'],
  snarky: ['…干嘛。', '戳够了没？', '哦？有事？', '哼，叫我干嘛。'],
  calm: ['嗯，我在。', '怎么了？', '请说。', '我在听。']
}

const POKE_MEDIUM: Record<ReactionTone, readonly string[]> = {
  gentle: ['再戳我会有点痒啦~', '知道啦知道啦！', '一直在戳我呢，嘿嘿~'],
  lively: ['太喜欢戳我了吧！', '戳戳戳！我收到啦~', '好啦好啦，我在呢！'],
  snarky: ['又戳？你是不是很闲。', '戳够了没有呀。', '行吧，戳吧。'],
  calm: ['我一直在。', '不必反复戳我。', '收到了，请说。']
}

const POKE_HEAVY: Record<ReactionTone, readonly string[]> = {
  gentle: ['别戳啦，脑瓜都晕了~', '好吧好吧，败给你啦！', '再戳我要撒娇啦！'],
  lively: ['啊啊啊别戳啦！', '戳爆啦！停下来嘛~', '我要反击啦！'],
  snarky: ['戳够了没有！哼！', '再戳我真的会生气哦。', '你是来戳我的还是来用我的？'],
  calm: ['请停止戳我。', '我已收到你的注意。', '够了，说正事吧。']
}

function pick(pool: readonly string[]): string {
  return pool[Math.floor(Math.random() * pool.length)]
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
  const text = pokeCount >= 5 ? pick(POKE_HEAVY[tone]) : pokeCount >= 3 ? pick(POKE_MEDIUM[tone]) : pick(POKE_LIGHT[tone])
  void speakProactive(text, { userInitiated: true })
}

const DRAG_REACTIONS: Record<ReactionTone, readonly string[]> = {
  gentle: ['呼，落地成功！', '把我搬到这里啦？', '站稳啦，新的好地方~'],
  lively: ['哇——飞起来啦！', '哎呀，搬到新家咯~', '落地！新地方探险！'],
  snarky: ['…随便搬。', '哼，又挪我。', '放下了？行吧。'],
  calm: ['已落地。', '位置已更新。', '好的，停在这里。']
}

let hoverThrottleTimer: ReturnType<typeof setTimeout> | null = null

export function handleHoverInteraction(): void {
  if (hoverThrottleTimer) {return}
  setSpriteState('interacting', { durationMs: 1500 })
  hoverThrottleTimer = setTimeout(() => {
    hoverThrottleTimer = null
  }, 10000)
}

export function handleDragEndInteraction(): void {
  recordInteraction('poke')
  setSpriteState('interacting', { durationMs: 2000 })
  void speakProactive(pick(DRAG_REACTIONS[personaTone()]), { userInitiated: true })
}
