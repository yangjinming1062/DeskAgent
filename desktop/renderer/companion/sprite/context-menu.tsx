import { useEffect, useRef } from 'react'

import { setChatOpen } from '@/companion/chat-store'
import { setSpriteState } from '@/companion/companion-store'
import { useInteractiveRegion } from '@/companion/interactive-regions'

interface ContextMenuProps {
  x: number
  y: number
  onClose: () => void
  onOpenVoiceCall: () => void
  onOpenSettings: () => void
}

export function SpriteContextMenu({ x, y, onClose, onOpenVoiceCall, onOpenSettings }: ContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null)
  useInteractiveRegion('sprite-context-menu', menuRef)

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
      className="fixed z-50 min-w-36 overflow-hidden rounded-xl border border-white/20 bg-black/80 p-1 text-xs text-white shadow-2xl backdrop-blur-xl animate-in fade-in zoom-in-95 duration-100 select-none"
      ref={menuRef}
      style={{ left: Math.min(x, window.innerWidth - 160), top: Math.min(y, window.innerHeight - 200) }}
    >
      <button
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left transition hover:bg-white/15"
        onClick={() => {
          setChatOpen(true)
          onClose()
        }}
        type="button"
      >
        <span>💬</span> 对话 (Talk)
      </button>
      <button
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left transition hover:bg-white/15"
        onClick={() => {
          onOpenVoiceCall()
          onClose()
        }}
        type="button"
      >
        <span>📞</span> 语音通话 (Voice)
      </button>
      <button
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left transition hover:bg-white/15"
        onClick={() => {
          onOpenSettings()
          onClose()
        }}
        type="button"
      >
        <span>🎛️</span> 伙伴设置
      </button>
      <button
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left transition hover:bg-white/15"
        onClick={() => {
          void window.deskagent.showToolWindow()
          onClose()
        }}
        type="button"
      >
        <span>⚙️</span> 应用设置 (Settings)
      </button>
      <button
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left transition hover:bg-white/15"
        onClick={() => {
          setSpriteState('sleeping')
          onClose()
        }}
        type="button"
      >
        <span>💤</span> 去睡觉 (Sleep)
      </button>
      <div className="my-1 h-px bg-white/10" />
      <button
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-red-400 transition hover:bg-red-500/20"
        onClick={() => {
          window.close()
          onClose()
        }}
        type="button"
      >
        <span>🚪</span> 退出 (Quit)
      </button>
    </div>
  )
}
