import { useStore } from '@nanostores/react'
import { IconPower, IconVolume, IconVolumeOff } from '@tabler/icons-react'
import { useCallback, useEffect, useRef } from 'react'

import type { DockKind } from '@/companion/companion-store'
import { resetToHomePosition, setDefaultScale, setLocale } from '@/companion/spatial'
import { Home, type IconComponent, KeyRound, MessageSquareText, Shirt, SlidersHorizontal } from '@/shared/lib/icons'
import { $auth } from '@/shared/store/auth'

import { $effectiveTier, closeChat, setDisturbanceTier } from '../companion-store'
import { isRegionHit, useInteractiveRegion } from '../interactive-regions'

import { $contextMenuPos, closeContextMenu } from './context-menu-store'

interface ContextMenuProps {
  onOpenActivation?: () => void
  onOpenChat: () => void
  onOpenDock: (kind: DockKind, view?: string) => void
}

const MENU_ITEM_CLASS =
  'flex h-8 w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 text-left text-xs font-medium text-body transition-colors hover:bg-fill-hover focus:bg-fill-hover focus:outline-none'

function MenuItem({
  icon: Icon,
  label,
  onClick
}: {
  icon: IconComponent
  label: string
  onClick: () => void
}): React.JSX.Element {
  return (
    <button
      className={MENU_ITEM_CLASS}
      onClick={() => {
        onClick()
        closeContextMenu()
      }}
      type="button"
    >
      <Icon className="size-4 shrink-0 text-muted" />
      <span className="min-w-0 flex-1 truncate">{label}</span>
    </button>
  )
}

function MenuDivider(): React.JSX.Element {
  return <div className="-mx-1.5 my-1 h-px bg-line-hairline" />
}

// 精灵右键菜单（瞬时浮层·轻玻璃档）：始终挂载、visibility 切换（避免 mount/unmount DOM），
// 状态走 $contextMenuPos 原子，宿主 CompanionRoot 不参与。页面切换由面板内
// 侧栏承担，菜单只负责开入口——与应用设置菜单形态一致。
export function SpriteContextMenu({ onOpenActivation, onOpenChat, onOpenDock }: ContextMenuProps): React.JSX.Element {
  const auth = useStore($auth)
  const pos = useStore($contextMenuPos)
  const effectiveTier = useStore($effectiveTier)
  const visible = pos !== null
  const authed = auth.kind === 'authenticated'
  const isStill = effectiveTier === 'still'

  const backdropRef = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  const toggleQuiet = () => {
    if (isStill) {
      setDisturbanceTier('normal')
    } else {
      setDisturbanceTier('still')
      // 50 分钟后自动恢复（spec §8）
      setTimeout(
        () => {
          if ($effectiveTier.get() === 'still') {
            setDisturbanceTier('normal')
          }
        },
        50 * 60 * 1000
      )
    }
  }

  const handleRest = () => {
    closeChat()
    resetToHomePosition()
    setDefaultScale(1)
    setLocale('home', { locomotion: 'fly' })
  }

  const handleQuit = () => {
    void window.spiritagent.sprite.hide()
  }

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

  const left = visible ? Math.min(pos.x, window.innerWidth - 200) : 0
  const top = visible ? Math.min(pos.y, window.innerHeight - 280) : 0

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
        className="fixed z-50 min-w-48 origin-top-left overflow-hidden rounded-xl border border-line-standard bg-surface-panel/95 p-1.5 text-xs text-strong shadow-2xl backdrop-blur-glass select-none transition-[opacity,transform] duration-150 ease-out"
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
            <MenuItem
              icon={isStill ? IconVolume : IconVolumeOff}
              label={isStill ? '可以吵我了' : '安静一会儿'}
              onClick={toggleQuiet}
            />
            <MenuItem icon={Home} label="去休息" onClick={handleRest} />
            <MenuDivider />
            <MenuItem icon={Shirt} label="换一身 / 形象" onClick={() => onOpenDock('outfit', 'wardrobe')} />
            <MenuItem icon={SlidersHorizontal} label="设置" onClick={() => onOpenDock('app-settings')} />
            <MenuItem icon={IconPower} label="退出" onClick={handleQuit} />
          </>
        ) : (
          <>
            <MenuItem icon={KeyRound} label="激活 / 登录" onClick={() => onOpenActivation?.()} />
            <MenuItem icon={SlidersHorizontal} label="设置" onClick={() => onOpenDock('app-settings')} />
            <MenuDivider />
            <MenuItem icon={IconPower} label="退出" onClick={handleQuit} />
          </>
        )}
      </div>
    </div>
  )
}
