import { useStore } from '@nanostores/react'
import { IconBrandWechat } from '@tabler/icons-react'
import type React from 'react'

import { FloatingPanel } from '@/companion'
import { AudioLines, Brain, Cpu, Info, Keyboard, Palette, Settings, Sparkles } from '@/shared/lib/icons'
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

import { $appSettingsView, type AppSettingsView } from './app-settings-view'

// 应用设置独立入口面板：八个应用层（原工具窗）。
export function AppSettingsPanel({ onClose }: { onClose: () => void }): React.JSX.Element {
  const view = useStore($appSettingsView)
  const t = strings

  const navItems: NavItemDescriptor[] = [
    { id: 'inference', label: t.settings.nav.inference, icon: Brain },
    { id: 'speech', label: t.speech.title, icon: AudioLines },
    { id: 'channels', label: t.settings.nav.channels, icon: IconBrandWechat },
    { id: 'appearance', label: t.settings.nav.appearance, icon: Palette },
    { id: 'shortcuts', label: t.settings.nav.shortcuts, icon: Keyboard },
    { id: 'runner', label: t.settings.nav.runner, icon: Cpu },
    { id: 'skills', label: t.settings.nav.skills, icon: Sparkles },
    { id: 'about', label: t.settings.nav.about, icon: Info }
  ]

  return (
    <FloatingPanel
      defaultSize={{ width: 960, height: 640 }}
      icon={Settings}
      onClose={onClose}
      regionId="companion-app-settings"
      static
      storagePrefix="da.companion.appSettingsPanel"
      title={t.settings.title}
    >
      <div className="flex min-h-0 flex-1">
        <aside
          className={cn(
            SURFACE_CHROME,
            'flex w-52 shrink-0 flex-col overflow-y-auto border-r border-line-standard p-2.5'
          )}
        >
          <SettingsNav activeId={view} items={navItems} onSelect={id => $appSettingsView.set(id as AppSettingsView)} />
        </aside>
        <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          {view === 'inference' ? (
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
