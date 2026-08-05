import { $gateway } from '@/shared/store/gateway'

import { $lastIdleSeconds, reportInteractionStat } from './activity'
import { $chatOpen, setProactiveBubble } from './chat-store'
import { setSpriteState } from './companion-store'
import { $effectiveTier } from './companion-store'
import { personaTone, type ReactionTone } from './persona-store'
import { speakProactive } from './proactive/proactive'

let lastPokeTime = 0
let pokeCount = 0
let resetTimer: ReturnType<typeof setTimeout> | null = null

// Persona-flavoured reaction pools (plan §4.3): same poke, different
// personality → different tone. Escalation (light→medium→heavy) is layered on
// top by frequency. LLM-driven enrichment is layered on top via the
// ``companion.interact`` RPC — see ``fetchLLMInteraction`` below.
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

// ----- LLM-enrichment overlay (companion.interact) --------------------------

const LLM_ENRICH_MIN_INTERVAL_MS = 2000
const LLM_FETCH_DEBOUNCE_MS = 200
const LLM_RESPONSE_MAX_CACHE = 8

let lastLLMInteractAt = 0
let pendingLLMTimer: ReturnType<typeof setTimeout> | null = null
let pendingLLMArgs: { tone: ReactionTone; pokeCount: number } | null = null
type InflightInteract = { cancelled: boolean }
let inflightInteract: InflightInteract | null = null
const cachedLLMByTone: Map<ReactionTone, string[]> = new Map()

function recordCachedLLM(tone: ReactionTone, text: string): void {
  const list = cachedLLMByTone.get(tone) ?? []
  list.push(text)

  if (list.length > LLM_RESPONSE_MAX_CACHE) {
    list.shift()
  }

  cachedLLMByTone.set(tone, list)
}

function popCachedLLM(tone: ReactionTone): string | null {
  const list = cachedLLMByTone.get(tone)

  if (!list || list.length === 0) {
    return null
  }

  return list.shift() ?? null
}

interface InteractResponse {
  text?: string
  emotion?: string | null
  reason?: string
}

async function fetchLLMInteraction(tone: ReactionTone, currentPokeCount: number): Promise<void> {
  const now = Date.now()

  if (now - lastLLMInteractAt < LLM_ENRICH_MIN_INTERVAL_MS) {
    return
  }

  // Resolve the gateway BEFORE consuming the throttle budget. Otherwise a
  // poke during a transient gateway outage (reconnect window) burns the
  // 2-second window without firing any RPC, starving the next poke.
  const gateway = $gateway.get()

  if (!gateway) {
    return
  }

  lastLLMInteractAt = now

  const tracker: InflightInteract = { cancelled: false }

  if (inflightInteract) {
    inflightInteract.cancelled = true
  }

  inflightInteract = tracker

  let response: InteractResponse | null = null

  try {
    response = await gateway.request<InteractResponse>('companion.interact', {
      kind: 'poke',
      tone,
      poke_count: currentPokeCount,
      // Backend expects idle_seconds (ARCH §4.2 contract); -1 means the
      // activity probe hasn't produced a reading yet, in which case we send
      // 0 to keep the LLM prompt populated without fabricating a long-idle
      // reaction. The persona-side context (personality, memories) carries
      // most of the signal anyway.
      idle_seconds: Math.max(0, $lastIdleSeconds.get()),
      local_hour: new Date().getHours()
    })
  } catch {
    return
  }

  if (tracker.cancelled || !response || !response.text) {
    return
  }

  const text = response.text.trim()

  if (!text) {
    return
  }

  recordCachedLLM(tone, text)

  // Avoid clobbering an already-playing TTS line. ``speakProactive`` was
  // already called synchronously with the local pool response; this
  // overlay is text-only (a brief bubble), letting the existing audio
  // finish naturally.
  if ($effectiveTier.get() === 'quiet') {
    return
  }

  if (!$chatOpen.get()) {
    setProactiveBubble(text)
  }
}

// ---------------------------------------------------------------------------

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

  const escalationBucket: 'light' | 'medium' | 'heavy' = pokeCount >= 5 ? 'heavy' : pokeCount >= 3 ? 'medium' : 'light'

  // For the first poke after a long pause (escalationBucket === 'light'),
  // prefer a cached LLM-generated line if one is available — this surfaces
  // the memory-aware enrichment without waiting for a new LLM call.
  // Cached lines were generated at varying pokeCount values; replaying a
  // heavy-burst line on a fresh single poke would break the light/medium/
  // heavy escalation invariant, so we only consume the cache for the
  // light bucket where the original generation was guaranteed to be light.
  const cached = escalationBucket === 'light' ? popCachedLLM(tone) : null

  const text =
    cached ??
    (escalationBucket === 'heavy'
      ? pick(POKE_HEAVY[tone])
      : escalationBucket === 'medium'
        ? pick(POKE_MEDIUM[tone])
        : pick(POKE_LIGHT[tone]))

  void speakProactive(text, { userInitiated: true })

  // Stats: every poke counts (including high-frequency bursts). Backend
  // aggregates across UTC days; rapid pokes just nudge today's counter.
  reportInteractionStat('poke')

  // LLM enrichment runs at most every 2s, and only as a *delayed* overlay
  // so it never interrupts the immediate local TTS. Track a single pending
  // timer + args so rapid pokes don't queue N callbacks; the latest poke
  // wins when the timer fires.
  pendingLLMArgs = { tone, pokeCount }

  if (pendingLLMTimer === null) {
    pendingLLMTimer = setTimeout(() => {
      pendingLLMTimer = null
      const args = pendingLLMArgs
      pendingLLMArgs = null

      if (args) {
        void fetchLLMInteraction(args.tone, args.pokeCount)
      }
    }, LLM_FETCH_DEBOUNCE_MS)
  }
}

const DRAG_REACTIONS: Record<ReactionTone, readonly string[]> = {
  gentle: ['呼，落地成功！', '把我搬到这里啦？', '站稳啦，新的好地方~'],
  lively: ['哇——飞起来啦！', '哎呀，搬到新家咯~', '落地！新地方探险！'],
  snarky: ['…随便搬。', '哼，又挪我。', '放下了？行吧。'],
  calm: ['已落地。', '位置已更新。', '好的，停在这里。']
}

let hoverThrottleTimer: ReturnType<typeof setTimeout> | null = null

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
  void speakProactive(pick(DRAG_REACTIONS[personaTone()]), { userInitiated: true })
  reportInteractionStat('drag')
}
