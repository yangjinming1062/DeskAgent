import { atom } from 'nanostores'

export type SpriteStateName =
  | 'idle'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'working'
  | 'emotional'
  | 'sleeping'
  | 'interacting'
  | 'disconnected'

export type SpriteEmotion =
  | 'happy'
  | 'sad'
  | 'surprised'
  | 'excited'
  | 'confused'
  | 'concerned'
  | 'shy'
  | 'proud'
  | 'grateful'
  | 'playful'
  | 'bored'
  | 'lonely'
  | 'sleepy'
  | 'curious'
  | 'embarrassed'
  | 'apologetic'

export const $spriteState = atom<SpriteStateName>('idle')
export const $spriteEmotion = atom<SpriteEmotion | null>(null)
export const $previousState = atom<SpriteStateName>('idle')

const STATE_PRIORITY: Record<SpriteStateName, number> = {
  disconnected: 100,
  interacting: 80,
  working: 70,
  speaking: 60,
  thinking: 50,
  listening: 40,
  emotional: 35,
  sleeping: 30,
  idle: 10
}

let transientTimer: ReturnType<typeof setTimeout> | null = null

export function setSpriteState(
  name: SpriteStateName,
  options?: { emotion?: SpriteEmotion; durationMs?: number; force?: boolean }
): void {
  const current = $spriteState.get()

  if (options?.force && transientTimer) {
    clearTimeout(transientTimer)
    transientTimer = null
  }

  if (name === 'emotional' || name === 'interacting') {
    if (current !== 'emotional' && current !== 'interacting') {
      $previousState.set(current)
    }
    if (options?.emotion) $spriteEmotion.set(options.emotion)
    $spriteState.set(name)

    if (transientTimer) clearTimeout(transientTimer)
    const ms = options?.durationMs ?? (name === 'emotional' ? 2500 : 1800)
    transientTimer = setTimeout(() => {
      transientTimer = null
      $spriteEmotion.set(null)
      const after = $spriteState.get()
      const prev = $previousState.get()
      $spriteState.set(
        after !== 'emotional' && after !== 'interacting'
          ? after
          : prev === 'emotional' || prev === 'interacting'
            ? 'idle'
            : prev
      )
    }, ms)
    return
  }

  // idle is always replaceable: even a future state with lower priority can
  // take over from idle. Otherwise, demotion requires force.
  if (!options?.force && STATE_PRIORITY[name] < STATE_PRIORITY[current] && current !== 'idle') return

  if (transientTimer) {
    clearTimeout(transientTimer)
    transientTimer = null
  }

  $spriteEmotion.set(options?.emotion ?? null)
  $spriteState.set(name)
}

