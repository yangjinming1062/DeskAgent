import { useEffect, useRef } from 'react'

import { setChatOpen } from '@/companion/chat-store'
import { setSpriteState } from '@/companion/companion-store'

interface ContextMenuProps {
  x: number
  y: number
  onClose: () => void
  onOpenVoiceCall: () => void
}

export function SpriteContextMenu({ x, y, onClose, onOpenVoiceCall }: ContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose()
      }
    }
    window.addEventListener('mousedown', handleClickOutside)
    return () => window.removeEventListener('mousedown', handleClickOutside)
  }, [onClose])

  return (
    <div
      ref={menuRef}
      className="fixed z-50 min-w-36 overflow-hidden rounded-xl border border-white/20 bg-black/80 p-1 text-xs text-white shadow-2xl backdrop-blur-xl animate-in fade-in zoom-in-95 duration-100 select-none"
      style={{ left: Math.min(x, window.innerWidth - 160), top: Math.min(y, window.innerHeight - 200) }}
    >
      <button
        type="button"
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left transition hover:bg-white/15"
        onClick={() => {
          setChatOpen(true)
          onClose()
        }}
      >
        <span>💬</span> 对话 (Talk)
      </button>
      <button
        type="button"
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left transition hover:bg-white/15"
        onClick={() => {
          onOpenVoiceCall()
          onClose()
        }}
      >
        <span>📞</span> 语音通话 (Voice)
      </button>
      <button
        type="button"
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left transition hover:bg-white/15"
        onClick={() => {
          void window.deskagent.showToolWindow()
          onClose()
        }}
      >
        <span>⚙️</span> 设置 (Settings)
      </button>
      <button
        type="button"
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left transition hover:bg-white/15"
        onClick={() => {
          setSpriteState('sleeping')
          onClose()
        }}
      >
        <span>💤</span> 去睡觉 (Sleep)
      </button>
      <div className="my-1 h-px bg-white/10" />
      <button
        type="button"
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-red-400 transition hover:bg-red-500/20"
        onClick={() => {
          window.close()
          onClose()
        }}
      >
        <span>🚪</span> 退出 (Quit)
      </button>
    </div>
  )
}
