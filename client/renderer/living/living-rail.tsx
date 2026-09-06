import { useStore } from '@nanostores/react'
import type React from 'react'

import { $spriteEmotion, $spriteState } from '@/companion/companion-store'
import { $persona } from '@/companion/persona-store'
import { $portraitUrl } from '@/companion/portrait-store'
import { triggerHaptic } from '@/shared/lib/haptics'
import {
  CalendarPlus,
  Globe,
  Home,
  type IconComponent,
  MessageSquareText,
  Palette,
  Settings,
  Shirt,
  Sparkles
} from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'

import { $livingView, type LivingView, setLivingView } from './living-store'
import styles from './living.module.css'

interface NavEntry {
  icon: IconComponent
  id: LivingView
  label: string
}

const NAV_ENTRIES: NavEntry[] = [
  { icon: MessageSquareText, id: 'chat', label: '对话' },
  { icon: Sparkles, id: 'moments', label: '片刻' },
  { icon: CalendarPlus, id: 'diary', label: '日记' },
  { icon: Shirt, id: 'wardrobe', label: '衣橱' },
  { icon: Palette, id: 'appearance', label: '形象' },
  { icon: Globe, id: 'channels', label: '通道' },
  { icon: Home, id: 'room', label: '房间' },
  { icon: Settings, id: 'settings', label: '设置' }
]

// 状态文案随动画状态走，不要写死"陪伴中 · 栖息"——静态文案会让人觉得她没在动。
function describeState(state: string, emotion: string | null): string {
  if (emotion && emotion !== 'neutral') {
    return `${stateLabel(state)} · ${emotionLabel(emotion)}`
  }

  return stateLabel(state)
}

function stateLabel(state: string): string {
  switch (state) {
    case 'thinking':
      return '在想事情'

    case 'working':
      return '在忙'

    case 'speaking':
      return '在说话'

    case 'listening':
      return '在听'

    case 'emotional':
      return '有小情绪'

    case 'interacting':
      return '在陪'

    default:
      return '陪伴中'
  }
}

function emotionLabel(emotion: string): string {
  switch (emotion) {
    case 'happy':
      return '开心'

    case 'sad':
      return '低落'

    case 'angry':
      return '闹别扭'

    case 'surprised':
      return '惊讶'

    case 'shy':
      return '害羞'

    case 'curious':
      return '好奇'

    case 'sleepy':
      return '犯困'

    case 'excited':
      return '兴奋'

    case 'playful':
      return '玩耍'

    case 'concerned':
      return '担心'

    case 'scared':
      return '害怕'

    default:
      return '陪伴中'
  }
}

export function LivingRail(): React.JSX.Element {
  const persona = useStore($persona)
  const portrait = useStore($portraitUrl)
  const view = useStore($livingView)
  const spriteState = useStore($spriteState)
  const emotion = useStore($spriteEmotion)
  const displayName = persona?.name ?? '伙伴'
  const statusText = describeState(spriteState, emotion)

  return (
    <aside className={styles.rail}>
      <div className={styles.identity}>
        <button
          aria-label={`${displayName} 的表情反馈`}
          className={styles.avatar}
          data-emotion={emotion && emotion !== 'neutral' ? emotion : undefined}
          data-state={spriteState}
          onClick={() => triggerHaptic('tap')}
          type="button"
        >
          {portrait ? (
            <img alt={displayName} className={styles.avatarImage} src={portrait} />
          ) : (
            <span className={styles.avatarFallback}>{displayName.slice(0, 1)}</span>
          )}
        </button>
        <div className={styles.identityText}>
          <p className={styles.displayName}>{displayName}</p>
          <span className={styles.statusDesc}>{statusText}</span>
        </div>
      </div>

      <nav className={styles.nav}>
        {NAV_ENTRIES.map(entry => {
          const Icon = entry.icon
          const isActive = view === entry.id

          return (
            <button
              className={cn(styles.navItem, isActive && styles.navItemActive)}
              key={entry.id}
              onClick={() => setLivingView(entry.id)}
              type="button"
            >
              <Icon className={styles.navItemIcon} />
              <span className={styles.navItemLabel}>{entry.label}</span>
            </button>
          )
        })}
      </nav>
    </aside>
  )
}
