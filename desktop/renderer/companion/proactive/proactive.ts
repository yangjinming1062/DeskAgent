import { $chatOpen, setProactiveBubble } from '@/companion/chat-store'
import { $disturbanceTier, setSpriteState } from '@/companion/companion-store'

import { speak } from '../tts'

export async function speakProactive(text: string, opts?: { userInitiated?: boolean; affect?: string }): Promise<void> {
  if (!text.trim()) {
    return
  }

  // Quiet tier suppresses proactive outreach, but user-initiated reactions
  // (poke/drag) always voice (plan §4.2). Affect is never gated — callers set
  // emotional state directly.
  const tier = $disturbanceTier.get()

  if (!opts?.userInitiated && tier === 'quiet') {
    return
  }

  if (!$chatOpen.get()) {
    setProactiveBubble(text.trim())
  }

  if (tier === 'proactive' || opts?.userInitiated) {
    // Force the speaking transition — priority 60 is otherwise gated silently
    // by 'working' (pri 70), so proactive/initiated speech wouldn't show.
    setSpriteState('speaking', { force: true })
    const ok = await speak(text)
    setSpriteState('idle')
    // Let the bubble linger briefly after the voice ends, then dismiss.
    const linger = ok ? 4200 : 5000
    setTimeout(() => setProactiveBubble(null), linger)
  } else {
    // Normal tier: longer linger so the text reads without spoken narration.
    setTimeout(() => setProactiveBubble(null), 8000)
  }
}
