import type { SpriteStateName } from '../state/companion-store'

/** Alternate clip names per state. The state's own name is tried first by
 * the resolver, so the canonical name is intentionally absent from these
 * alias lists. Names follow common conventions from Mixamo, Ready Player
 * Me, and our own asset-pack naming. */
const STATE_CLIP_ALIASES: Record<SpriteStateName, string[]> = {
  idle: ['Idle', 'Idle_Neutral', 'breathing_idle', 'Breathing Idle'],
  listening: ['Listen', 'Idle_Listening', 'listening_idle'],
  thinking: ['Think', 'Thinking', 'Idle_Thinking', 'thinking_pose'],
  speaking: ['Talk', 'Talking', 'talking gesture', 'talking'],
  working: ['Work', 'Working', 'typing', 'Typing', 'computer_typing'],
  sleeping: ['Sleep', 'Sleeping', 'lying_down', 'sleep_pose'],
  interacting: ['wave', 'Wave', 'Waving', 'interacting', 'greeting'],
  emotional: ['Idle', 'Idle_Neutral'],
  disconnected: ['Sleep', 'Sleeping', 'Idle']
}

export function resolveClip(
  state: SpriteStateName,
  available: Set<string>
): string | null {
  if (available.has(state)) return state
  for (const alias of STATE_CLIP_ALIASES[state] ?? []) {
    if (available.has(alias)) return alias
  }
  return null
}
