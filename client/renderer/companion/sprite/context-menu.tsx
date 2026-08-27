import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { isRegionHit, useInteractiveRegion } from '@/companion/interactive-regions'
import { $renderMode } from '@/companion/mesh2d/mesh2d-store'
import type { SettingsView } from '@/companion/settings/settings-view'
import {
  AudioLines,
  Brain,
  ChevronRight,
  EyeOff,
  KeyRound,
  MessageSquareText,
  Palette,
  Phone,
  Settings,
  Shirt,
  SlidersHorizontal,
  Zap
} from '@/shared/lib/icons'
import type { IconComponent } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { $auth } from '@/shared/store/auth'

import { $contextMenuPos, closeContextMenu } from './context-menu-store'

interface ContextMenuProps {
  onOpenActivation?: () => void
  onOpenChat: () => void
  onOpenVoiceCall: () => void
  /** 直达伙伴设置的指定页面（计划 §二 的五页 IA）。 */
  onOpenSettingsPage: (page: SettingsView) => void
}

const MENU_ITEM_CLASS =
  'flex h-8 w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 text-left text-xs font-medium text-white/85 transition-colors hover:bg-white/10 focus:bg-white/10 focus:outline-none'

function MenuItem({
  icon: Icon,
  label,
  onClick,
  trailing
}: {
  icon: IconComponent
  label: string
  onClick: () => void
  trailing?: React.ReactNode
}): React.JSX.Element {
  return (
    <button
      className={MENU_ITEM_CLASS}
      onClick={() => {
        onClick()
        closeContextMenu()
      }}
      onPointerDown={e => e.stopPropagation()}
      type="button"
    >
      <Icon className="size-4 shrink-0 text-white/45" />
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {trailing}
    </button>
  )
}

function MenuDivider(): React.JSX.Element {
  return <div className="-mx-1.5 my-1 h-px bg-white/8" />
}

// 精灵右键菜单（瞬时浮层·轻玻璃档）：始终挂载、visibility 切换（避免 mount/unmount DOM），
// 状态走 $contextMenuPos 原子，宿主 CompanionRoot 不参与。
// 「伙伴设置」按五页 IA 展开子菜单直达（衣柜仅 2D 渲染模式显示）。
export function SpriteContextMenu({
  onOpenActivation,
  onOpenChat,
  onOpenVoiceCall,
  onOpenSettingsPage
}: ContextMenuProps): React.JSX.Element {
  const auth = useStore($auth)
  const renderMode = useStore($renderMode)
  const pos = useStore($contextMenuPos)
  const visible = pos !== null
  const authed = auth.kind === 'authenticated'
  const [settingsSubOpen, setSettingsSubOpen] = useState(false)
  const backdropRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  const getInteractiveRect = useCallback(
    () => (visible && pos ? new DOMRect(0, 0, window.innerWidth, window.innerHeight) : null),
    [visible, pos]
  )

  useInteractiveRegion('sprite-context-menu', backdropRef, getInteractiveRect)

  useEffect(() => {
    if (!visible) {
      setSettingsSubOpen(false)
    }
  }, [visible])

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

  const left = visible && pos ? Math.min(pos.x, window.innerWidth - 220) : 0
  const top = visible && pos ? Math.min(pos.y, window.innerHeight - 280) : 0
  // 靠近右缘时子菜单向左翻开，避免截断。
  const submenuSide = visible && pos && pos.x > window.innerWidth - 420 ? 'left' : 'right'

  const settingsPages: Array<{ icon: IconComponent; id: SettingsView; label: string }> = [
    { icon: Brain, id: 'persona', label: '角色与记忆' },
    { icon: AudioLines, id: 'voice', label: '音色' },
    ...(renderMode === '2d' ? [{ icon: Shirt, id: 'wardrobe' as SettingsView, label: '衣柜' }] : []),
    { icon: Palette, id: 'appearance', label: '形象' },
    { icon: Zap, id: 'interaction', label: '交互' }
  ]

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
        className="fixed z-50 min-w-48 origin-top-left overflow-visible rounded-xl border border-white/12 bg-black/65 p-1.5 text-xs text-white shadow-2xl backdrop-blur-lg select-none transition-[opacity,transform] duration-150 ease-out"
        onPointerDown={e => {
          e.stopPropagation()
        }}
        ref={menuRef}
        style={{
          left,
          top,
          opacity: visible ? 1 : 0,
          pointerEvents: visible ? 'auto' : 'none',
          transform: visible ? 'scale(1)' : 'scale(0.96)'
        }}
      >
        {authed ? (
          <>
            <MenuItem icon={MessageSquareText} label="对话" onClick={onOpenChat} />
            <MenuItem icon={Phone} label="语音通话" onClick={onOpenVoiceCall} />
            <MenuDivider />

            <div className="relative" onPointerEnter={() => setSettingsSubOpen(true)}>
              <button
                className={cn(MENU_ITEM_CLASS, settingsSubOpen && 'bg-white/10')}
                onClick={() => setSettingsSubOpen(s => !s)}
                onPointerDown={e => e.stopPropagation()}
                type="button"
              >
                <SlidersHorizontal className="size-4 shrink-0 text-white/45" />
                <span className="min-w-0 flex-1 truncate">伙伴设置</span>
                <ChevronRight
                  className={cn('size-3.5 text-white/35 transition-transform', settingsSubOpen && 'rotate-90')}
                />
              </button>

              {settingsSubOpen && (
                <div
                  className={
                    submenuSide === 'right'
                      ? 'absolute left-full top-0 ml-1.5 min-w-40 rounded-xl border border-white/12 bg-[#141416] p-1 shadow-2xl'
                      : 'absolute right-full top-0 mr-1.5 min-w-40 rounded-xl border border-white/12 bg-[#141416] p-1 shadow-2xl'
                  }
                  onPointerEnter={() => setSettingsSubOpen(true)}
                  onPointerLeave={() => setSettingsSubOpen(false)}
                >
                  {settingsPages.map(page => (
                    <MenuItem
                      icon={page.icon}
                      key={page.id}
                      label={page.label}
                      onClick={() => onOpenSettingsPage(page.id)}
                    />
                  ))}
                </div>
              )}
            </div>

            <MenuItem icon={Settings} label="应用设置" onClick={() => void window.spiritagent.showToolWindow()} />
            <MenuDivider />
            <MenuItem icon={EyeOff} label="隐藏" onClick={() => void window.spiritagent.sprite.hide()} />
          </>
        ) : (
          <>
            <MenuItem icon={KeyRound} label="激活 / 登录" onClick={() => onOpenActivation?.()} />
            <MenuItem icon={Settings} label="应用设置" onClick={() => void window.spiritagent.showToolWindow()} />
            <MenuDivider />
            <MenuItem icon={EyeOff} label="隐藏" onClick={() => void window.spiritagent.sprite.hide()} />
          </>
        )}
      </div>
    </div>
  )
}
