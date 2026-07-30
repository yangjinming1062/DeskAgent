// Receives a proactive companion message (design.md §5.1.A `companion.message`,
// emitted by the Backend's send_message path) and presents it: TTS + a bubble
// when chat is closed. Gated by the disturbance tier — `quiet` blocks the
// companion's proactive outreach (but never the user's own actions).
import { $chatOpen, setProactiveBubble } from '@/companion/chat-store'
import { $disturbanceTier, setSpriteState } from '@/companion/companion-store'

import { speak } from './tts'

export async function speakProactive(text: string): Promise<void> {
  if (!text.trim()) return
  // 保持安静档断消息通道、不断 affect (design.md §6) — affect is phase 2, so
  // here we simply suppress the proactive utterance.
  if ($disturbanceTier.get() === 'quiet') return

  if (!$chatOpen.get()) setProactiveBubble(text.trim())
  setSpriteState('speaking')
  const ok = await speak(text)
  setSpriteState('idle')
  // Let the bubble linger briefly after the voice ends, then dismiss.
  const linger = ok ? 4200 : 5000
  setTimeout(() => setProactiveBubble(null), linger)
}
