import { useStore } from '@nanostores/react'

import { $chatOpen, $proactiveBubble } from '@/companion/chat-store'
import { $spatialPos, $spatialScale, $viewport, computeOverlayAnchorBesideSprite } from '@/companion/spatial'

// Transient bubble for a proactive companion message, shown beside the
// companion when the chat dock is closed (plan.md §4.2). When chat is open the
// message already lives in the transcript, so we don't double-show.
//
// Anchored beside the sprite so it follows drag / walk / fly / chat-locale
// repositioning; the outer gate short-circuits when there's no message so the
// spatial subscriptions only run while a bubble is on screen.
const BUBBLE_GAP = 8
const BUBBLE_MAX_W = 256
const BUBBLE_VERTICAL_RATIO = 0.1

export function ProactiveBubble(): React.JSX.Element | null {
  const text = useStore($proactiveBubble)
  const chatOpen = useStore($chatOpen)

  if (!text || chatOpen) {
    return null
  }

  return <ProactiveBubbleView text={text} />
}

function ProactiveBubbleView({ text }: { text: string }): React.JSX.Element {
  const pos = useStore($spatialPos)
  const scale = useStore($spatialScale)
  const viewport = useStore($viewport)

  const { left, top } = computeOverlayAnchorBesideSprite({
    pos,
    scale,
    gap: BUBBLE_GAP,
    overlayMaxW: BUBBLE_MAX_W,
    vw: viewport.width,
    vh: viewport.height,
    verticalRatio: BUBBLE_VERTICAL_RATIO
  })

  return (
    <div className="proactive-bubble fixed z-30 max-w-[16rem]" style={{ left, top }}>
      <style>{`@keyframes proactiveIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}.proactive-bubble>span{animation:proactiveIn .25s ease-out}`}</style>
      <span className="block rounded-2xl rounded-br-sm border border-white/10 bg-black/65 px-3.5 py-2 text-sm leading-relaxed text-white/90 shadow-xl backdrop-blur-md">
        💬 {text}
      </span>
    </div>
  )
}
