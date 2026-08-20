import type { SpriteStateName } from '@/companion/companion-store'

// 动画名严格遵循 MODEL_SPEC.md §3。供应商必须按规范命名,客户端只匹配规范名。
// resolveClip 按顺序尝试名称,首个命中即返回；全部不匹配 → null,引擎走回退。

const STATE_CLIPS: Record<SpriteStateName, string[]> = {
  idle: ['idle'],
  listening: ['listening'],
  thinking: ['thinking'],
  speaking: ['speaking'],
  working: ['working'],
  sleeping: ['sleeping'],
  interacting: ['interacting'],
  emotional: ['emotional_idle'],
  disconnected: ['disconnected']
}

export function resolveClip(state: SpriteStateName, available: Set<string>): string | null {
  for (const name of STATE_CLIPS[state] ?? ['idle']) {
    if (available.has(name)) {
      return name
    }
  }

  return null
}
