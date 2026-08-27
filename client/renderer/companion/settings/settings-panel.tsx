import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import { $renderMode } from '@/companion/mesh2d/mesh2d-store'
import { FloatingPanel } from '@/companion/panel/floating-panel'
import { AudioLines, Brain, Palette, Shirt, SlidersHorizontal, Zap } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { type NavItemDescriptor, SettingsNav, SURFACE_CHROME } from '@/shared/panel'

import { AppearancePage } from './pages/appearance-page'
import { InteractionPage } from './pages/interaction-page'
import { PersonaMemoryPage } from './pages/persona-memory-page'
import { VoicePage } from './pages/voice-page'
import { WardrobePage } from './pages/wardrobe-page'
import { $settingsView, type SettingsView } from './settings-view'

// 伙伴设置分页外壳。衣柜页仅 2D 渲染模式可见（3D 模型不随服装变）；
// 页面停留在衣柜时被切到 3D 则回落到形象页。
export function CompanionSettings({ onClose }: { onClose: () => void }): React.JSX.Element {
  const view = useStore($settingsView)
  const renderMode = useStore($renderMode)

  useEffect(() => {
    if (view === 'wardrobe' && renderMode !== '2d') {
      $settingsView.set('appearance')
    }
  }, [view, renderMode])

  const navItems: NavItemDescriptor[] = [
    { id: 'persona', label: '角色与记忆', icon: Brain },
    { id: 'voice', label: '音色', icon: AudioLines },
    ...(renderMode === '2d' ? [{ id: 'wardrobe', label: '衣柜', icon: Shirt }] : []),
    { id: 'appearance', label: '形象', icon: Palette },
    { id: 'interaction', label: '交互', icon: Zap }
  ]

  return (
    <FloatingPanel
      defaultSize={{ width: 960, height: 640 }}
      icon={SlidersHorizontal}
      maxSize={{ width: 1280, height: 900 }}
      minSize={{ width: 720, height: 540 }}
      onClose={onClose}
      regionId="companion-settings"
      storagePrefix="da.companion.settings"
      title="伙伴设置"
    >
      <div className="flex min-h-0 flex-1">
        <aside className={cn(SURFACE_CHROME, 'flex w-52 shrink-0 flex-col border-r border-white/10 p-2.5')}>
          <SettingsNav activeId={view} items={navItems} onSelect={id => $settingsView.set(id as SettingsView)} />
        </aside>
        {view === 'persona' ? (
          <PersonaMemoryPage />
        ) : view === 'voice' ? (
          <VoicePage />
        ) : view === 'wardrobe' ? (
          <WardrobePage />
        ) : view === 'appearance' ? (
          <AppearancePage />
        ) : (
          <InteractionPage />
        )}
      </div>
    </FloatingPanel>
  )
}
