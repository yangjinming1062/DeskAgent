import { useStore } from '@nanostores/react'
import { useEffect, useRef } from 'react'

import { $renderStyle } from '@/companion/3d/model-store'
import { setChatOpen } from '@/companion/chat-store'
import { setSpriteState } from '@/companion/companion-store'
import { useInteractiveRegion } from '@/companion/interactive-regions'
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
  onOpenVoiceCall: () => void
  onOpenSettings: () => void
  onOpenMemory: () => void
}

export function SpriteContextMenu({
  onOpenActivation,
  onOpenVoiceCall,
  onOpenSettings,
  onOpenMemory
}: ContextMenuProps): React.JSX.Element {
  const auth = useStore($auth)
  const pos = useStore($contextMenuPos)
  const renderStyle = useStore($renderStyle)
  const visible = pos !== null
  const authed = auth.kind === 'authenticated'
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

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        closeContextMenu()
      }
    }

    window.addEventListener('mousedown', handleClickOutside)
    window.addEventListener('keydown', handleKeyDown)

    return () => {
      window.removeEventListener('mousedown', handleClickOutside)
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [visible])

  const left = visible && pos ? Math.min(pos.x, window.innerWidth - 180) : 0
  const top = visible && pos ? Math.min(pos.y, window.innerHeight - 240) : 0

  return (
    <div
      className="fixed z-50 min-w-44 overflow-hidden rounded-xl border border-(--ui-stroke-secondary) bg-[color-mix(in_srgb,var(--ui-bg-elevated)_94%,transparent)] p-1 text-xs text-foreground shadow-2xl backdrop-blur-xl select-none"
      ref={menuRef}
      style={{
        left,
        top,
        visibility: visible ? 'visible' : 'hidden',
        // Belt-and-suspenders: prevent the hidden menu from intercepting any tile it overlaps.
        pointerEvents: visible ? 'auto' : 'none'
      }}
    >
      {!authed ? (
        <>
          <button
            className="flex w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-xs font-medium text-foreground transition-colors hover:bg-(--ui-control-active-background) focus:bg-(--ui-control-active-background) focus:outline-none"
            onClick={() => {
              onOpenActivation?.()
              closeContextMenu()
            }}
            type="button"
          >
            <KeyRound className="size-3.5 text-(--ui-text-tertiary) shrink-0" />
            <span>激活 / 登录 (Login)</span>
          </button>
          <button
            className="flex w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-xs font-medium text-foreground transition-colors hover:bg-(--ui-control-active-background) focus:bg-(--ui-control-active-background) focus:outline-none"
            onClick={() => {
              void window.spiritagent.showToolWindow()
              closeContextMenu()
            }}
            type="button"
          >
            <Settings className="size-3.5 text-(--ui-text-tertiary) shrink-0" />
            <span>应用设置 (Settings)</span>
          </button>
          <div className="-mx-1 my-1 h-px bg-(--ui-stroke-tertiary)" />
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
            className="flex w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-xs font-medium text-foreground transition-colors hover:bg-(--ui-control-active-background) focus:bg-(--ui-control-active-background) focus:outline-none"
            onClick={() => {
              setChatOpen(true)
              closeContextMenu()
            }}
            type="button"
          >
            <MessageSquareText className="size-3.5 text-(--ui-text-tertiary) shrink-0" />
            <span>对话 (Talk)</span>
          </button>
          <button
            className="flex w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-xs font-medium text-foreground transition-colors hover:bg-(--ui-control-active-background) focus:bg-(--ui-control-active-background) focus:outline-none"
            onClick={() => {
              onOpenVoiceCall()
              closeContextMenu()
            }}
            type="button"
          >
            <Phone className="size-3.5 text-(--ui-text-tertiary) shrink-0" />
            <span>语音通话 (Voice)</span>
          </button>
          <button
            className="flex w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-xs font-medium text-foreground transition-colors hover:bg-(--ui-control-active-background) focus:bg-(--ui-control-active-background) focus:outline-none"
            onClick={() => {
              onOpenSettings()
              closeContextMenu()
            }}
            type="button"
          >
            <SlidersHorizontal className="size-3.5 text-(--ui-text-tertiary) shrink-0" />
            <span>伙伴设置</span>
          </button>
          <button
            className="flex w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-xs font-medium text-foreground transition-colors hover:bg-(--ui-control-active-background) focus:bg-(--ui-control-active-background) focus:outline-none"
            onClick={() => {
              $renderStyle.set(renderStyle === 'anime' ? 'realistic' : 'anime')
              closeContextMenu()
            }}
            type="button"
          >
            <Palette className="size-3.5 text-(--ui-text-tertiary) shrink-0" />
            <span>渲染风格：{renderStyle === 'anime' ? '二次元' : '写实'}</span>
          </button>
          <button
            className="flex w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-xs font-medium text-foreground transition-colors hover:bg-(--ui-control-active-background) focus:bg-(--ui-control-active-background) focus:outline-none"
            onClick={() => {
              onOpenMemory()
              closeContextMenu()
            }}
            type="button"
          >
            <Brain className="size-3.5 text-(--ui-text-tertiary) shrink-0" />
            <span>长期记忆</span>
          </button>
          <button
            className="flex w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-xs font-medium text-foreground transition-colors hover:bg-(--ui-control-active-background) focus:bg-(--ui-control-active-background) focus:outline-none"
            onClick={() => {
              void window.spiritagent.showToolWindow()
              closeContextMenu()
            }}
            type="button"
          >
            <Settings className="size-3.5 text-(--ui-text-tertiary) shrink-0" />
            <span>应用设置 (Settings)</span>
          </button>
          <button
            className="flex w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-xs font-medium text-foreground transition-colors hover:bg-(--ui-control-active-background) focus:bg-(--ui-control-active-background) focus:outline-none"
            onClick={() => {
              setSpriteState('sleeping')
              closeContextMenu()
            }}
            type="button"
          >
            <Moon className="size-3.5 text-(--ui-text-tertiary) shrink-0" />
            <span>去睡觉 (Sleep)</span>
          </button>
          <div className="-mx-1 my-1 h-px bg-(--ui-stroke-tertiary)" />
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
  )
}
