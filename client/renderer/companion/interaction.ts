import { $gateway } from '@/shared/store/gateway'
import type { ReactionBucket } from '@/shared/types/reactions'

import { resolveClip } from './3d/AnimationMap'
import { $availableClipNames, $clipMap } from './3d/model-store'
import { $lastIdleSeconds, reportInteractionStat } from './activity'
import { $clipOverride, $spriteAction, $spriteEmotion, setSpriteState } from './companion-store'
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
  action?: string | null
}

interface InteractRpcRequest {
  kind: 'poke'
  poke_count: number
  idle_seconds: number
  local_hour: number
  /** mesh2d 子区域命中（head/face/arm_L/arm_R/body/back_hair/front_hair/skirt）；
   *  不传 = 整精灵矩形命中。3D 路径走 silhouette hit，2D 路径由 mesh2d-hitmap.ts 提供。 */
  region?: string
}

const LLM_INTERACT_COOLDOWN_MS = 5 * 60 * 1000

function playLocalReaction(bucket: ReactionBucket, tags: string[]): void {
  const entry = pickReaction(bucket, tags)

  void playReactionAudio(entry)
}

async function triggerReaction(bucket: ReactionBucket, tags: string[], region?: string): Promise<void> {
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
    const request: InteractRpcRequest = {
      kind: 'poke',
      poke_count: pokeCount,
      idle_seconds: Math.max(0, $lastIdleSeconds.get()),
      local_hour: new Date().getHours()
    }

    if (region) {
      request.region = region
    }

    // gateway.request 期望 Record<string, unknown>；展开成普通对象
    const res = await gateway.request<InteractRpcResponse>('companion.interact', { ...request })

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

      // LLM 可顺带返回 action（mesh2d 路径走 driver 兑现，3D 路径走 clip map）
      if (res.action) {
        // 直接写入 $spriteAction 让 driver 看到（events.ts 通常也会写；这里 LLM RPC 是额外来源）
        $spriteAction.set(res.action)
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

export function handlePokeInteraction(region?: string): void {
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

  void triggerReaction(bucket, tags, region)
  reportInteractionStat('poke')
}

export function handleHoverInteraction(
  region?: string,
  impulseMagnet?: (boneName: string, magnitude: number) => void
): void {
  // 命中 hair / skirt 等区域时给 driver 触发 impulse（头发 / 裙子物理抖动）
  // magnet 是 Mesh2DCanvas 注入的回调，避免 interaction.ts 直接依赖 driver
  if (!region || !impulseMagnet) {
    return
  }

  switch (region) {
    case 'back_hair':

    case 'front_hair':
      impulseMagnet(region, 2.5)

      break

    case 'skirt':
      impulseMagnet('skirt', 3.0)

      break

    default:
      // 其他区域（head / face / arm / body）暂无 impulse 反馈
      break
  }
}

export function handleDragEndInteraction(): void {
  playLocalReaction('drag', $personalityTags.get())
}
