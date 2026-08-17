import { $gateway } from '@/shared/store/gateway'
import type { ReactionBucket } from '@/shared/types/reactions'

import { resolveInteractionClip } from './3d/clip-dispatch'
import { getClipDefs } from './3d/clips-registry'
import { $availableClipNames, $modelInfo } from './3d/model-store'
import { $lastIdleSeconds, reportInteractionStat } from './activity'
import { $clipOverride, $spriteEmotion, setSpriteState } from './companion-store'
import { $personalityTags } from './persona-store'
import { $llmReactions } from './prefs'
import { pickReaction, playReactionAudio } from './reactions/reaction-audio'

let lastPokeTime = 0
let pokeCount = 0
let resetTimer: ReturnType<typeof setTimeout> | null = null
let hoverThrottleTimer: ReturnType<typeof setTimeout> | null = null
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

  // Optimistic claim — refunded below unless we get an LLM reaction or the
  // server says rate_limited, so one transient failure doesn't lock out 5 min.
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

    // Server-side cost window still active for poke — sync our clock
    // and fall back to the local pool.
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

    // llm_error / unparseable / inflight — refund so the next poke retries.
    lastLlmPokeAt = 0
  } catch {
    // Network / timeout — refund so the next poke retries.
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
  const library = getClipDefs($modelInfo.get().rig_type)
  const available = $availableClipNames.get()
  const bucket = bucketForPokeCount()

  const clip = resolveInteractionClip(bucket, tags, library, available)
  $clipOverride.set(clip)
  setSpriteState('interacting', { durationMs: 2000 })

  void triggerReaction(bucket, tags)
  reportInteractionStat('poke')
}

export function handleHoverInteraction(): void {
  // Smooth hover: cursor tracking is handled directly by Look-At without interrupting clips
}

export function handleDragEndInteraction(): void {
  playLocalReaction('drag', $personalityTags.get())
}
