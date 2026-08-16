import { useStore } from '@nanostores/react'
import { useEffect, useRef } from 'react'

import { setChatOpen } from '@/companion/chat-store'
import { setSpriteState } from '@/companion/companion-store'
import { useInteractiveRegion } from '@/companion/interactive-regions'

import { $contextMenuPos, closeContextMenu } from './context-menu-store'

interface ContextMenuProps {
  onOpenVoiceCall: () => void
  onOpenSettings: () => void
  onOpenMemory: () => void
}

export function SpriteContextMenu({
  onOpenVoiceCall,
  onOpenSettings,
  onOpenMemory
}: ContextMenuProps): React.JSX.Element {
  const pos = useStore($contextMenuPos)
  const visible = pos !== null
  const menuRef = useRef<HTMLDivElement>(null)
  // Hidden state returns null so isPointInteractive skips the menu — avoids the (0,0) false hit that display:none would introduce (BCR returns 0×0).
  useInteractiveRegion('sprite-context-menu', menuRef, () => {
    if (!visible || !pos) {
      return null
    }

    const el = menuRef.current

    if (!el) {
      return null
    }

    const rect = el.getBoundingClientRect()

    if (rect.width === 0 || rect.height === 0) {
      return null
    }

    return new DOMRect(rect.left, rect.top, rect.width, rect.height)
  })

  useEffect(() => {
    if (!visible) {
      return
    }

    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        closeContextMenu()
      }
    }

    window.addEventListener('mousedown', handleClickOutside)

    return () => window.removeEventListener('mousedown', handleClickOutside)
  }, [visible])

  const left = visible && pos ? Math.min(pos.x, window.innerWidth - 160) : 0
  const top = visible && pos ? Math.min(pos.y, window.innerHeight - 200) : 0

  return (
    <div
      className="fixed z-50 min-w-36 overflow-hidden rounded-xl border border-white/20 bg-black/85 p-1 text-xs text-white shadow-2xl select-none"
      ref={menuRef}
      style={{
        left,
        top,
        visibility: visible ? 'visible' : 'hidden',
        // Belt-and-suspenders: prevent the hidden menu from intercepting any tile it overlaps.
        pointerEvents: visible ? 'auto' : 'none'
      }}
    >
      <button
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left transition hover:bg-white/15"
        onClick={() => {
          setChatOpen(true)
          closeContextMenu()
        }}
        type="button"
      >
        <span>💬</span> 对话 (Talk)
      </button>
      <button
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left transition hover:bg-white/15"
        onClick={() => {
          onOpenVoiceCall()
          closeContextMenu()
        }}
        type="button"
      >
        <span>📞</span> 语音通话 (Voice)
      </button>
      <button
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left transition hover:bg-white/15"
        onClick={() => {
          onOpenSettings()
          closeContextMenu()
        }}
        type="button"
      >
        <span>🎛️</span> 伙伴设置
      </button>
      <button
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left transition hover:bg-white/15"
        onClick={() => {
          onOpenMemory()
          closeContextMenu()
        }}
        type="button"
      >
        <span>🧠</span> 长期记忆
      </button>
      <button
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left transition hover:bg-white/15"
        onClick={() => {
          void window.spiritagent.showToolWindow()
          closeContextMenu()
        }}
        type="button"
      >
        <span>⚙️</span> 应用设置 (Settings)
      </button>
      <button
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left transition hover:bg-white/15"
        onClick={() => {
          setSpriteState('sleeping')
          closeContextMenu()
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
          closeContextMenu()
        }}
        type="button"
      >
        <span>🚪</span> 退出 (Quit)
      </button>
    </div>
  )
}
