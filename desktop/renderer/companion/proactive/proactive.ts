// Receives a proactive companion message (ARCHITECTURE.md §5.1.A `companion.message`,
// emitted by the Backend's send_message path) and presents it: TTS + a bubble
// when chat is closed. Gated by the disturbance tier — `quiet` blocks the
// companion's proactive outreach (but never the user's own actions).
import { $chatOpen, setProactiveBubble } from '@/companion/chat-store'
import { $disturbanceTier, setSpriteState } from '@/companion/companion-store'

import { speak } from '../tts'

export async function speakProactive(text: string, opts?: { userInitiated?: boolean }): Promise<void> {
  if (!text.trim()) {return}

  // Quiet tier suppresses the companion's proactive outreach, but user-
  // initiated reactions (poke/drag) always voice (plan §4.2: 用户主动发起的
  // 交互永远不受限). Affect is never gated — callers set emotional state directly.
  if (!opts?.userInitiated && $disturbanceTier.get() === 'quiet') {return}

  if (!$chatOpen.get()) {setProactiveBubble(text.trim())}
  setSpriteState('speaking')
  const ok = await speak(text)
  setSpriteState('idle')
  // Let the bubble linger briefly after the voice ends, then dismiss.
  const linger = ok ? 4200 : 5000
  setTimeout(() => setProactiveBubble(null), linger)
}
