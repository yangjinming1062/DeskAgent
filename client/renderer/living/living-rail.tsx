import { useStore } from '@nanostores/react'
import type React from 'react'

import { $persona } from '@/companion/persona-store'
import { $portraitUrl } from '@/companion/portrait-store'
import {
  ArrowRight,
  CalendarPlus,
  Globe,
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
  { icon: MessageSquareText, id: 'chat', label: '陪伴对话' },
  { icon: Sparkles, id: 'moments', label: '生活片刻' },
  { icon: CalendarPlus, id: 'diary', label: '自然日记' },
  { icon: Shirt, id: 'wardrobe', label: '衣橱换装' },
  { icon: Palette, id: 'appearance', label: '伙伴形象' },
  { icon: Globe, id: 'channels', label: '跨端通道' },
  { icon: Settings, id: 'settings', label: '设置中心' }
]

interface LivingRailProps {
  onGoToWorkbench: () => void
}

export function LivingRail({ onGoToWorkbench }: LivingRailProps): React.JSX.Element {
  const persona = useStore($persona)
  const portrait = useStore($portraitUrl)
  const view = useStore($livingView)
  const displayName = persona?.name ?? '伙伴'

  return (
    <aside className={styles.rail}>
      <div className={styles.identity}>
        <div className={styles.avatar}>
          {portrait ? (
            <img alt={displayName} className={styles.avatarImage} src={portrait} />
          ) : (
            <span className={styles.avatarFallback}>{displayName.slice(0, 1)}</span>
          )}
        </div>
        <div className={styles.identityText}>
          <p className={styles.displayName}>{displayName}</p>
          <span className={styles.statusDesc}>陪伴中 · 栖息</span>
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
              <span>{entry.label}</span>
            </button>
          )
        })}
      </nav>

      <button className={styles.workbenchButton} onClick={onGoToWorkbench} style={{ marginTop: 'auto' }} type="button">
        <span>切换到工作台</span>
        <ArrowRight size={13} />
      </button>
    </aside>
  )
}
