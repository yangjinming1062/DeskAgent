import type { SpriteStateName } from '@/companion/companion-store'

// Clip names follow GLB_MODEL_SPEC.md §3. The spec's canonical name for each
// state is the first entry; fallbacks handle Mixamo / Ready Player Me / ad-hoc
// models that predate the spec. resolveClip tries names in order, first hit
// wins. If none match, returns null — the engine falls back to procedural.

const STATE_CLIPS: Record<SpriteStateName, string[]> = {
  // Spec §3.1 MUST clips
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
