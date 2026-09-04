import { useStore } from '@nanostores/react'
import type React from 'react'

import { FloatingPanel } from '@/companion'
import { AudioLines, Brain, SlidersHorizontal, Zap } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { type NavItemDescriptor, SettingsNav, SURFACE_CHROME } from '@/shared/panel'

import { $companionSettingsView, type CompanionSettingsView } from './companion-settings-view'
import { InteractionPage } from './pages/interaction-page'
import { PersonaMemoryPage } from './pages/persona-memory-page'
import { VoicePage } from './pages/voice-page'

// 伙伴设置独立入口面板：ChatDock 顶栏与音色失效通知直达此处。
export function CompanionSettingsPanel({ onClose }: { onClose: () => void }): React.JSX.Element {
  const view = useStore($companionSettingsView)

  const navItems: NavItemDescriptor[] = [
    { id: 'persona', label: '角色与记忆', icon: Brain },
    { id: 'voice', label: '音色', icon: AudioLines },
    { id: 'interaction', label: '交互', icon: Zap }
  ]

  return (
    <FloatingPanel
      defaultSize={{ width: 960, height: 640 }}
      icon={SlidersHorizontal}
      onClose={onClose}
      regionId="companion-settings"
      static
      storagePrefix="da.companion.companionSettingsPanel"
      title="伙伴设置"
    >
      <div className="flex min-h-0 flex-1">
        <aside
          className={cn(
            SURFACE_CHROME,
            'flex w-52 shrink-0 flex-col overflow-y-auto border-r border-line-standard p-2.5'
          )}
        >
          <SettingsNav
            activeId={view}
            items={navItems}
            onSelect={id => $companionSettingsView.set(id as CompanionSettingsView)}
          />
        </aside>
        <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          {view === 'persona' ? <PersonaMemoryPage /> : view === 'voice' ? <VoicePage /> : <InteractionPage />}
        </main>
      </div>
    </FloatingPanel>
  )
}
