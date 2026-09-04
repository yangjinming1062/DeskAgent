import { useStore } from '@nanostores/react'
import { IconBrandWechat } from '@tabler/icons-react'
import { useEffect } from 'react'

import { $renderMode } from '@/2d'
import { FloatingPanel } from '@/companion'
import {
  AudioLines,
  Brain,
  Cpu,
  Info,
  Keyboard,
  Palette,
  Shirt,
  SlidersHorizontal,
  Sparkles,
  Zap
} from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { type NavItemDescriptor, SettingsNav, SURFACE_CHROME } from '@/shared/panel'
import { strings } from '@/shared/strings'

import { AboutSettings } from '../settings/about-settings'
import { AppearanceSettings } from '../settings/appearance-settings'
import { ChannelsSettings } from '../settings/channels-settings'
import { InferenceSettings } from '../settings/inference-settings'
import { RunnerSettings } from '../settings/runner-settings'
import { ShortcutsSettings } from '../settings/shortcuts-settings'
import { SkillsToolsTabs } from '../settings/skills-tools-tabs'
import { SpeechSettings } from '../settings/speech-settings'

import { AppearancePage } from './pages/appearance-page'
import { InteractionPage } from './pages/interaction-page'
import { PersonaMemoryPage } from './pages/persona-memory-page'
import { VoicePage } from './pages/voice-page'
import { WardrobePage } from './pages/wardrobe-page'
import { $settingsView, type SettingsView } from './settings-view'

// 合并后的「设置」面板：八个应用层（原工具窗）+ 五个伙伴层（原 companion panel）。
// 物理形态是 sprite 透明工作区内的 FloatingPanel，居中展示并由 interactive region 接管点击穿透。
// 衣柜页仅 2D 渲染模式可见（3D 模型不随服装变）；停留在衣柜时被切到 3D 则回落到「形象」页。
export function CompanionSettings({ onClose }: { onClose: () => void }): React.JSX.Element {
  const view = useStore($settingsView)
  const renderMode = useStore($renderMode)

  useEffect(() => {
    if (view === 'wardrobe' && renderMode !== '2d') {
      $settingsView.set('sprite-appearance')
    }
  }, [view, renderMode])

  const t = strings

  const navItems: NavItemDescriptor[] = [
    { id: 'inference', label: t.settings.nav.inference, icon: Brain },
    { id: 'speech', label: t.speech.title, icon: AudioLines },
    { id: 'channels', label: t.settings.nav.channels, icon: IconBrandWechat },
    { id: 'appearance', label: t.settings.nav.appearance, icon: Palette },
    { id: 'shortcuts', label: t.settings.nav.shortcuts, icon: Keyboard },
    { id: 'runner', label: t.settings.nav.runner, icon: Cpu },
    { id: 'skills', label: t.settings.nav.skills, icon: Sparkles },
    { id: 'persona', label: '角色与记忆', icon: Brain },
    { id: 'voice', label: '音色', icon: AudioLines },
    ...(renderMode === '2d' ? [{ id: 'wardrobe', label: '衣柜', icon: Shirt }] : []),
    { id: 'sprite-appearance', label: '形象', icon: Palette },
    { id: 'interaction', label: '交互', icon: Zap },
    { id: 'about', label: t.settings.nav.about, icon: Info }
  ]

  return (
    <FloatingPanel
      defaultSize={{ width: 960, height: 640 }}
      icon={SlidersHorizontal}
      onClose={onClose}
      regionId="companion-settings"
      static
      storagePrefix="da.companion.settingsPanel"
      title="设置"
    >
      <div className="flex min-h-0 flex-1">
        <aside
          className={cn(
            SURFACE_CHROME,
            'flex w-52 shrink-0 flex-col overflow-y-auto border-r border-line-standard p-2.5'
          )}
        >
          <SettingsNav activeId={view} items={navItems} onSelect={id => $settingsView.set(id as SettingsView)} />
        </aside>
        <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          {view === 'persona' ? (
            <PersonaMemoryPage />
          ) : view === 'voice' ? (
            <VoicePage />
          ) : view === 'wardrobe' ? (
            <WardrobePage />
          ) : view === 'sprite-appearance' ? (
            <AppearancePage />
          ) : view === 'interaction' ? (
            <InteractionPage />
          ) : view === 'inference' ? (
            <InferenceSettings />
          ) : view === 'speech' ? (
            <SpeechSettings />
          ) : view === 'channels' ? (
            <ChannelsSettings />
          ) : view === 'appearance' ? (
            <AppearanceSettings />
          ) : view === 'shortcuts' ? (
            <ShortcutsSettings />
          ) : view === 'runner' ? (
            <RunnerSettings />
          ) : view === 'skills' ? (
            <SkillsToolsTabs />
          ) : view === 'about' ? (
            <AboutSettings />
          ) : null}
        </main>
      </div>
    </FloatingPanel>
  )
}
