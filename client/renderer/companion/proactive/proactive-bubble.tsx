import { useStore } from '@nanostores/react'
import { useRef } from 'react'

import { $chatOpen, $proactiveBubble, setChatOpen } from '@/companion/chat-store'
import { useInteractiveRegion } from '@/companion/interactive-regions'
import { $spatialPos, $spatialScale, $viewport, computeOverlayAnchorBesideSprite } from '@/companion/spatial'

// 伙伴主动消息的临时气泡：聊天面板关闭时显示在伙伴身边（plan.md §4.2）。
// 聊天面板打开时，消息已经在对话流里出现，这里不再重复显示。
//
// 锚定在精灵身边，跟随拖拽 / 行走 / 飞行 / 聊天场所重新定位；
// 外层在「无消息」时短路掉，保证 spatial 订阅只在气泡显示期间才跑。
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
  const bubbleRef = useRef<HTMLDivElement>(null)

  useInteractiveRegion('proactive-bubble', bubbleRef)

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
    <div
      className="proactive-bubble fixed z-30 max-w-[16rem] cursor-pointer select-none"
      onClick={() => setChatOpen(true)}
      ref={bubbleRef}
      style={{ left, top }}
    >
      <style>{`@keyframes proactiveIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}.proactive-bubble>span{animation:proactiveIn .25s ease-out}`}</style>
      <span className="block rounded-2xl rounded-br-sm border border-white/10 bg-black/65 px-3.5 py-2 text-sm leading-relaxed text-white/90 shadow-xl backdrop-blur-md transition hover:bg-black/80 hover:text-white">
        💬 {text}
      </span>
    </div>
  )
}
