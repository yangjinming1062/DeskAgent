import type { SpriteStateName } from '@/companion/companion-store'

// 动作名遵循 MODEL_SPEC.md §3。每个状态的规范名作为第一条；
// 备选项兼容 Mixamo / Ready Player Me 等早于规范的临时模型。
// resolveClip 按顺序尝试名称，首个命中即返回。
// 若全部不匹配则返回 null——引擎回退到程序化兜底。

const STATE_CLIPS: Record<SpriteStateName, string[]> = {
  // 规范 §3.1 必选动作
  idle: ['idle', 'Idle', 'Idle_Neutral', 'breathing_idle'],
  listening: ['listening', 'Listen', 'Idle_Listening'],
  thinking: ['thinking', 'Think', 'Thinking', 'Idle_Thinking'],
  speaking: ['speaking', 'Talk', 'Talking'],
  working: ['working', 'Work', 'Working', 'typing', 'Typing'],
  sleeping: ['sleeping', 'Sleep', 'Sleeping'],
  interacting: ['interacting', 'poke_reaction_light', 'wave', 'Wave'],
  emotional: ['emotional_idle', 'idle', 'Idle'],
  disconnected: ['disconnected', 'Sleep', 'Sleeping', 'idle']
}

export function resolveClip(state: SpriteStateName, available: Set<string>): string | null {
  for (const name of STATE_CLIPS[state] ?? ['idle']) {
    if (available.has(name)) {
      return name
    }
  }

  return null
}
