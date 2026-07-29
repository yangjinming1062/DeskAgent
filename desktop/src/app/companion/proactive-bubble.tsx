import { useStore } from '@nanostores/react'

import { $chatOpen, $proactiveBubble } from '@/store/chat'

// Transient bubble for a proactive companion message, shown beside the
// companion when the chat dock is closed (plan.md §4.2). When chat is open the
// message already lives in the transcript, so we don't double-show.
export function ProactiveBubble() {
  const text = useStore($proactiveBubble)
  const chatOpen = useStore($chatOpen)

  if (!text || chatOpen) return null

  return (
    <div className="proactive-bubble fixed bottom-28 right-8 z-30 max-w-[16rem]">
      <style>{`@keyframes proactiveIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}.proactive-bubble>span{animation:proactiveIn .25s ease-out}`}</style>
      <span className="block rounded-2xl rounded-br-sm border border-white/10 bg-black/65 px-3.5 py-2 text-sm leading-relaxed text-white/90 shadow-xl backdrop-blur-md">
        💬 {text}
      </span>
    </div>
  )
}
