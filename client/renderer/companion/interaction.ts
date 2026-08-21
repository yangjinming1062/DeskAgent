import { $gateway } from '@/shared/store/gateway'
import type { ReactionBucket } from '@/shared/types/reactions'

import { resolveClip } from './3d/AnimationMap'
import { $availableClipNames, $clipMap } from './3d/model-store'
import { $lastIdleSeconds, reportInteractionStat } from './activity'
import { $clipOverride, $spriteEmotion, setSpriteState } from './companion-store'
import { $personalityTags } from './persona-store'
import { $llmReactions } from './prefs'
import { pickReaction, playReactionAudio } from './reactions/reaction-audio'

let lastPokeTime = 0
let pokeCount = 0
let resetTimer: ReturnType<typeof setTimeout> | null = null
let lastLlmPokeAt = 0

export function resetPokeLlmCooldown(): void {
  lastLlmPokeAt = 0
}

function bucketForPokeCount(): ReactionBucket {
  if (pokeCount >= 5) {
    return 'poke-heavy'
  }

  if (pokeCount >= 3) {
    return 'poke-medium'
  }

  return 'poke-light'
}

interface InteractRpcResponse {
  text?: string | null
  emotion?: string | null
  reason?: string
}

const LLM_INTERACT_COOLDOWN_MS = 5 * 60 * 1000

function playLocalReaction(bucket: ReactionBucket, tags: string[]): void {
  const entry = pickReaction(bucket, tags)

  void playReactionAudio(entry)
}

async function triggerReaction(bucket: ReactionBucket, tags: string[]): Promise<void> {
  const now = Date.now()
  const useLlm = $llmReactions.get()
  const inLlmCooldown = now - lastLlmPokeAt < LLM_INTERACT_COOLDOWN_MS

  if (!useLlm || inLlmCooldown) {
    playLocalReaction(bucket, tags)

    return
  }

  // 乐观抢占——除非拿到 LLM 反应或服务端返回 rate_limited，否则下方会回退，
  // 这样一次瞬时失败不会锁死 5 分钟。
  lastLlmPokeAt = now

  const gateway = $gateway.get()

  if (!gateway) {
    lastLlmPokeAt = 0
    playLocalReaction(bucket, tags)

    return
  }

  try {
    const res = await gateway.request<InteractRpcResponse>('companion.interact', {
      kind: 'poke',
      poke_count: pokeCount,
      idle_seconds: Math.max(0, $lastIdleSeconds.get()),
      local_hour: new Date().getHours()
    })

    // 服务端的成本窗口对 poke 仍生效——同步本地时钟并退回回本地反应池。
    if (res?.reason === 'rate_limited') {
      lastLlmPokeAt = Date.now()
      playLocalReaction(bucket, tags)

      return
    }

    if (res?.text) {
      if (res.emotion) {
        $spriteEmotion.set(res.emotion)
      }

      void playReactionAudio({ id: 'llm-poke', text: res.text, tags: [], bucket })

      return
    }

    // llm_error / 无法解析 / inflight——退还本次抢占，让下次 poke 重试。
    lastLlmPokeAt = 0
  } catch {
    // 网络 / 超时——退还本次抢占，让下次 poke 重试。
    lastLlmPokeAt = 0
  }

  playLocalReaction(bucket, tags)
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
  const bucket = bucketForPokeCount()

  // 音效资产按 kebab 分档，线上语义键统一 snake 且不分档。
  $clipOverride.set(resolveClip('poke', $clipMap.get(), $availableClipNames.get()))
  setSpriteState('interacting', { durationMs: 2000 })

  void triggerReaction(bucket, tags)
  reportInteractionStat('poke')
}

export function handleHoverInteraction(): void {
  // 平滑悬停：光标追踪由 Look-At 直接处理，不打断动画
}

export function handleDragEndInteraction(): void {
  playLocalReaction('drag', $personalityTags.get())
}
