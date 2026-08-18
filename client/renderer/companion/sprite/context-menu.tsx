import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef } from 'react'

import { $renderStyle } from '@/companion/3d/model-store'
import { setSpriteState } from '@/companion/companion-store'
import { isRegionHit, useInteractiveRegion } from '@/companion/interactive-regions'
import {
  Brain,
  KeyRound,
  LogOut,
  MessageSquareText,
  Moon,
  Palette,
  Phone,
  Settings,
  SlidersHorizontal
} from '@/shared/lib/icons'
import { $auth } from '@/shared/store/auth'

import { $contextMenuPos, closeContextMenu } from './context-menu-store'

interface ContextMenuProps {
  onOpenActivation?: () => void
  onOpenChat: () => void
  onOpenVoiceCall: () => void
  onOpenSettings: () => void
  onOpenMemory: () => void
}

export function SpriteContextMenu({
  onOpenActivation,
  onOpenChat,
  onOpenVoiceCall,
  onOpenSettings,
  onOpenMemory
}: ContextMenuProps): React.JSX.Element {
  const auth = useStore($auth)
  const pos = useStore($contextMenuPos)
  const renderStyle = useStore($renderStyle)
  const visible = pos !== null
  const authed = auth.kind === 'authenticated'
  const backdropRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  const getInteractiveRect = useCallback(
    () => (visible && pos ? new DOMRect(0, 0, window.innerWidth, window.innerHeight) : null),
    [visible, pos]
  )

  useInteractiveRegion('sprite-context-menu', backdropRef, getInteractiveRect)

  useEffect(() => {
    if (!visible) {
      return
    }

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        closeContextMenu()
      }
    }

    const handleBlur = () => {
      closeContextMenu()
    }

    window.addEventListener('keydown', handleKeyDown)
    window.addEventListener('blur', handleBlur)

    return () => {
      window.removeEventListener('keydown', handleKeyDown)
      window.removeEventListener('blur', handleBlur)
    }
  }, [visible])

  const left = visible && pos ? Math.min(pos.x, window.innerWidth - 180) : 0
  const top = visible && pos ? Math.min(pos.y, window.innerHeight - 240) : 0

  return (
    <div
      className="fixed inset-0 z-50 select-none"
      onContextMenu={e => {
        e.preventDefault()
        e.stopPropagation()

        if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
          if (isRegionHit('sprite-stage', e.clientX, e.clientY)) {
            $contextMenuPos.set({ x: e.clientX, y: e.clientY })
          } else {
            closeContextMenu()
          }
        }
      }}
      onPointerDown={e => {
        if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
          e.preventDefault()
          e.stopPropagation()
          closeContextMenu()
        }
      }}
      ref={backdropRef}
      style={{
        pointerEvents: visible ? 'auto' : 'none',
        visibility: visible ? 'visible' : 'hidden'
      }}
    >
      <div
        className="fixed z-50 min-w-44 overflow-hidden rounded-xl border border-white/10 bg-black/60 p-1 text-xs text-white shadow-2xl backdrop-blur-md select-none"
        onPointerDown={e => {
          e.stopPropagation()
        }}
        ref={menuRef}
        style={{
          left,
          top,
          pointerEvents: visible ? 'auto' : 'none'
        }}
      >
        {!authed ? (
          <>
            <button
              className="flex w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-xs font-medium text-white/90 transition-colors hover:bg-white/10 focus:bg-white/10 focus:outline-none"
              onClick={() => {
                onOpenActivation?.()
                closeContextMenu()
              }}
              type="button"
            >
              <KeyRound className="size-3.5 text-white/50 shrink-0" />
              <span>激活 / 登录 (Login)</span>
            </button>
            <button
              className="flex w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-xs font-medium text-white/90 transition-colors hover:bg-white/10 focus:bg-white/10 focus:outline-none"
              onClick={() => {
                void window.spiritagent.showToolWindow()
                closeContextMenu()
              }}
              type="button"
            >
              <Settings className="size-3.5 text-white/50 shrink-0" />
              <span>应用设置 (Settings)</span>
            </button>
            <div className="-mx-1 my-1 h-px bg-white/10" />
            <button
              className="flex w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-xs font-medium text-destructive transition-colors hover:bg-destructive/15 focus:bg-destructive/15 focus:outline-none"
              onClick={() => {
                window.close()
                closeContextMenu()
              }}
              type="button"
            >
              <LogOut className="size-3.5 text-destructive shrink-0" />
              <span>退出 (Quit)</span>
            </button>
          </>
        ) : (
          <>
            <button
              className="flex w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-xs font-medium text-white/90 transition-colors hover:bg-white/10 focus:bg-white/10 focus:outline-none"
              onClick={() => {
                onOpenChat()
                closeContextMenu()
              }}
              type="button"
            >
              <MessageSquareText className="size-3.5 text-white/50 shrink-0" />
              <span>对话 (Talk)</span>
            </button>
            <button
              className="flex w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-xs font-medium text-white/90 transition-colors hover:bg-white/10 focus:bg-white/10 focus:outline-none"
              onClick={() => {
                onOpenVoiceCall()
                closeContextMenu()
              }}
              type="button"
            >
              <Phone className="size-3.5 text-white/50 shrink-0" />
              <span>语音通话 (Voice)</span>
            </button>
            <button
              className="flex w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-xs font-medium text-white/90 transition-colors hover:bg-white/10 focus:bg-white/10 focus:outline-none"
              onClick={() => {
                onOpenSettings()
                closeContextMenu()
              }}
              type="button"
            >
              <SlidersHorizontal className="size-3.5 text-white/50 shrink-0" />
              <span>伙伴设置 (Companion)</span>
            </button>
            <button
              className="flex w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-xs font-medium text-white/90 transition-colors hover:bg-white/10 focus:bg-white/10 focus:outline-none"
              onClick={() => {
                $renderStyle.set(renderStyle === 'anime' ? 'realistic' : 'anime')
                closeContextMenu()
              }}
              type="button"
            >
              <Palette className="size-3.5 text-white/50 shrink-0" />
              <span>渲染风格 (Style)：{renderStyle === 'anime' ? '二次元' : '写实'}</span>
            </button>
            <button
              className="flex w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-xs font-medium text-white/90 transition-colors hover:bg-white/10 focus:bg-white/10 focus:outline-none"
              onClick={() => {
                onOpenMemory()
                closeContextMenu()
              }}
              type="button"
            >
              <Brain className="size-3.5 text-white/50 shrink-0" />
              <span>长期记忆 (Memory)</span>
            </button>
            <button
              className="flex w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-xs font-medium text-white/90 transition-colors hover:bg-white/10 focus:bg-white/10 focus:outline-none"
              onClick={() => {
                void window.spiritagent.showToolWindow()
                closeContextMenu()
              }}
              type="button"
            >
              <Settings className="size-3.5 text-white/50 shrink-0" />
              <span>应用设置 (Settings)</span>
            </button>
            <button
              className="flex w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-xs font-medium text-white/90 transition-colors hover:bg-white/10 focus:bg-white/10 focus:outline-none"
              onClick={() => {
                setSpriteState('sleeping')
                closeContextMenu()
              }}
              type="button"
            >
              <Moon className="size-3.5 text-white/50 shrink-0" />
              <span>去睡觉 (Sleep)</span>
            </button>
            <div className="-mx-1 my-1 h-px bg-white/10" />
            <button
              className="flex w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-xs font-medium text-destructive transition-colors hover:bg-destructive/15 focus:bg-destructive/15 focus:outline-none"
              onClick={() => {
                window.close()
                closeContextMenu()
              }}
              type="button"
            >
              <LogOut className="size-3.5 text-destructive shrink-0" />
              <span>退出 (Quit)</span>
            </button>
          </>
        )}
      </div>
    </div>
  )
}
